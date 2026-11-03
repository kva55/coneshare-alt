import logging
import os
from pathlib import Path
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from core.models import User
from documents.models import Folder, Document
from documents.services import (
    delete_document_and_files, _get_unique_document_name, _get_unique_folder_name,
    recalculate_user_document_size
)

from .models import Dataroom, DataroomDocument, DataroomFolder

logger = logging.getLogger(__name__)


def touch_dataroom_folder_ancestors(folder: DataroomFolder | None):
    """
    Updates the modification timestamp (updated_at) for the given DataroomFolder
    and all its ancestor folders up to the root in a single batch UPDATE query.
    """
    if not folder:
        return
    now = timezone.now()
    ancestor_ids = []
    visited = set()
    current = folder
    while current and current.id not in visited:
        visited.add(current.id)
        ancestor_ids.append(current.id)
        current = current.parent
    if ancestor_ids:
        DataroomFolder.objects.filter(id__in=ancestor_ids).update(updated_at=now)


def get_or_create_dataroom_storage_folder(dataroom: Dataroom, requesting_user=None, relative_path=None) -> Folder:
    """
    Resolves or creates the physical Folder structure for a Dataroom (system vault):
    Org Root -> '__datarooms__' (created_by=None) -> '<dataroom_id>' (created_by=None) -> relative subfolders.
    `requesting_user` is accepted for call-site compatibility and is not used.
    """
    organization = dataroom.organization
    root_folder = Folder.objects.get_root_for_org(organization)

    if dataroom.vault_folder:
        dataroom_folder = dataroom.vault_folder
    else:
        system_vault, _ = Folder.objects.get_or_create(
            organization=organization,
            parent=root_folder,
            name="__datarooms__",
            created_by=None,
            folder_type=Folder.FOLDER_TYPE_VAULT,
        )
        dataroom_folder, _ = Folder.objects.get_or_create(
            organization=organization,
            parent=system_vault,
            name=str(dataroom.id),
            created_by=None,
            folder_type=Folder.FOLDER_TYPE_VAULT,
        )
        dataroom.vault_folder = dataroom_folder
        dataroom.save(update_fields=['vault_folder'])

    current_folder = dataroom_folder

    if relative_path:
        folder_path, _ = os.path.split(relative_path)
        if folder_path:
            path_parts = Path(folder_path).parts
            for part in path_parts:
                current_folder, _ = Folder.get_or_create_vault_subfolder(
                    organization=organization,
                    parent=current_folder,
                    name=part,
                )
    return current_folder


def delete_dataroom(dataroom: Dataroom):
    """
    Explicitly deletes a Dataroom:
    1. Removes all dataroom items using reference-aware cleanup (preserving documents
       that are linked in other datarooms).
    2. Deletes the backing system vault folder (__datarooms__/<id>).
    3. Deletes the Dataroom model record.
    """
    organization = dataroom.organization
    root_folder = Folder.objects.get_root_for_org(organization)

    # 1. Clean up all Dataroom items with reference counting
    all_doc_ids = list(dataroom.documents.values_list('id', flat=True))
    all_folder_ids = list(dataroom.folders.values_list('id', flat=True))
    if all_doc_ids or all_folder_ids:
        remove_dataroom_content(dataroom, dataroom_doc_ids=all_doc_ids, dataroom_folder_ids=all_folder_ids)

    # 2. Clean up the backing system vault folder
    if root_folder:
        storage_folder = dataroom.vault_folder
        system_vault = None
        if not storage_folder:
            system_vault = Folder.objects.filter(
                organization=organization,
                parent=root_folder,
                name="__datarooms__",
                folder_type=Folder.FOLDER_TYPE_VAULT,
            ).first()
            if system_vault:
                storage_folder = Folder.objects.filter(
                    organization=organization,
                    parent=system_vault,
                    name=str(dataroom.id),
                    folder_type=Folder.FOLDER_TYPE_VAULT,
                ).first()
        else:
            system_vault = storage_folder.parent

        if storage_folder:
            descendants = storage_folder.get_descendants()
            folders_to_delete = [storage_folder] + descendants

            # When remove_dataroom_content() ran above, it preserved backing documents that
            # are still referenced by other datarooms. Because Document.folder cascades on
            # delete, deleting storage_folder or its subfolders would destroy those surviving
            # shared documents. We must relocate them up to the parent system_vault first.
            #
            # Note: Documents from different subfolders may share the same name (or collide
            # with existing vault docs). Relocate them individually with _get_unique_document_name
            # to satisfy the unique_active_document_name (created_by, folder, name) constraint
            # and avoid IntegrityError 500 crashes.
            remaining_docs = list(
                Document.objects.filter(folder__in=folders_to_delete).select_related('created_by')
            )
            for doc in remaining_docs:
                unique_name = _get_unique_document_name(doc.created_by, system_vault, doc.name)
                doc.name = unique_name
                doc.folder = system_vault
                doc.save(update_fields=['name', 'folder'])
            try:
                Folder.objects.filter(id__in=[f.id for f in folders_to_delete]).delete()
            except Exception as e:
                logger.exception("Failed to clean up system vault folder for dataroom %s: %s", dataroom.id, e)

    # 3. Delete the Dataroom record
    dataroom.delete()


def remove_dataroom_content(dataroom: Dataroom, dataroom_doc_ids: list = None, dataroom_folder_ids: list = None):
    """
    Explicitly removes documents and folders from a Dataroom:
    1. Recursively expands all descendant folder IDs within the specified dataroom_folder_ids.
    2. Collects all DataroomDocument associations being deleted (supporting both DataroomDocument.id
       and backing Document.id matching, as well as documents contained in deleted folders).
    3. Identifies direct-upload documents that have no remaining references across any datarooms.
       Evaluates all selected associations per Document to prevent order-dependent skips, and marks
       the backing Document and storage files for physical deletion.
    4. Deletes DataroomDocument and DataroomFolder records, and cleans up backing storage/files for
       surviving unreferenced direct-upload documents.
    """
    dataroom_doc_ids = dataroom_doc_ids or []
    dataroom_folder_ids = dataroom_folder_ids or []

    # 1. Expand all descendant folder IDs
    all_folder_ids = set(dataroom_folder_ids)
    if dataroom_folder_ids:
        folders_to_check = list(DataroomFolder.objects.filter(id__in=dataroom_folder_ids, dataroom=dataroom))
        stack = list(folders_to_check)
        while stack:
            f = stack.pop()
            for child in f.children.all():
                all_folder_ids.add(child.id)
                stack.append(child)

    # 2. Collect all DataroomDocument objects being removed
    # Allow dataroom_doc_ids to match either DataroomDocument.id or workspace Document.id
    ddocs_to_remove = list(
        DataroomDocument.objects.filter(
            Q(id__in=dataroom_doc_ids) | Q(document_id__in=dataroom_doc_ids) | Q(folder_id__in=all_folder_ids),
            dataroom=dataroom
        ).select_related('document', 'document__folder', 'folder')
    )
    ddoc_ids_to_remove = {d.id for d in ddocs_to_remove}

    # 3. Identify direct upload documents that have no other references
    # A single Document may have multiple DataroomDocument associations in the same dataroom
    # (e.g. directly uploaded to a subfolder and also imported or linked elsewhere via API/legacy).
    # We group all associations by backing Document and check whether ANY association in the group
    # represents a direct upload. This eliminates order-dependent skipping (which occurred when an
    # arbitrary seen_doc_ids loop encountered a linked association before a direct-upload association)
    # and ensures physical storage files are reliably reclaimed whenever all references to a direct
    # upload are removed.
    backing_docs_to_delete = []
    docs_to_ddocs = {}
    for ddoc in ddocs_to_remove:
        if ddoc.document:
            docs_to_ddocs.setdefault(ddoc.document, []).append(ddoc)

    for doc, ddocs in docs_to_ddocs.items():
        if any(is_direct_upload_dataroom_document(d) for d in ddocs):
            # Check if this backing document is still referenced by any surviving DataroomDocument
            # (either in this dataroom or other datarooms) that is NOT being removed.
            other_references_exist = (
                DataroomDocument.objects.filter(document=doc)
                .exclude(id__in=ddoc_ids_to_remove)
                .exists()
            )
            if not other_references_exist:
                backing_docs_to_delete.append(doc)

    # 4. Perform deletion
    parent_folders_to_touch = set()
    for ddoc in ddocs_to_remove:
        if ddoc.folder and ddoc.folder.id not in all_folder_ids:
            parent_folders_to_touch.add(ddoc.folder)

    if dataroom_folder_ids:
        folders_being_deleted = DataroomFolder.objects.filter(
            id__in=dataroom_folder_ids,
            dataroom=dataroom
        ).select_related('parent')
        for folder in folders_being_deleted:
            if folder.parent and folder.parent.id not in all_folder_ids:
                parent_folders_to_touch.add(folder.parent)

    with transaction.atomic():
        if ddoc_ids_to_remove:
            DataroomDocument.objects.filter(id__in=ddoc_ids_to_remove, dataroom=dataroom).delete()
        if dataroom_folder_ids:
            DataroomFolder.objects.filter(id__in=dataroom_folder_ids, dataroom=dataroom).delete()
        for parent_folder in parent_folders_to_touch:
            touch_dataroom_folder_ancestors(parent_folder)

    # Delete backing documents and storage files for direct uploads with no other references (non-blocking)
    for doc in backing_docs_to_delete:
        try:
            delete_document_and_files(doc, is_vault=True)
        except Exception as e:
            logger.exception("Failed to clean up backing document %s: %s", doc.id, e)


def is_direct_upload_dataroom_document(ddoc: DataroomDocument) -> bool:
    """
    Determines if a DataroomDocument is a direct upload (vault-owned) vs linked from library.
    1. Fast path: If is_direct_upload is already explicitly set (True or False), returns immediately.
    2. Fallback path (Legacy records with is_direct_upload=None): Inspects ancestor folder hierarchy,
       and self-heals by saving the resolved boolean to DB.
    """
    if ddoc.is_direct_upload is not None:
        return ddoc.is_direct_upload

    doc = ddoc.document
    if not doc or not doc.folder_id:
        return False

    if doc.folder and doc.folder.folder_type == Folder.FOLDER_TYPE_VAULT:
        is_direct = True
    else:
        root_folder = Folder.objects.get_root_for_org(doc.organization)
        if not root_folder:
            return False

        storage_root_ids = set(Folder.objects.filter(
            organization=doc.organization,
            parent=root_folder,
            name="__datarooms__"
        ).values_list('id', flat=True))

        curr = doc.folder
        is_direct = False
        while curr:
            if curr.id in storage_root_ids:
                is_direct = True
                break
            curr = curr.parent

    try:
        DataroomDocument.objects.filter(id=ddoc.id).update(is_direct_upload=is_direct)
        ddoc.is_direct_upload = is_direct
    except Exception as e:
        logger.warning(
            "Failed to self-heal is_direct_upload for DataroomDocument %s: %s",
            ddoc.id,
            e,
        )

    return is_direct


def sync_dataroom_folder_rename(dfolder: DataroomFolder, old_name: str, new_name: str):
    """
    Synchronizes the backing physical Folder name when a visual DataroomFolder is renamed.
    """
    if not new_name or new_name == old_name:
        return

    dataroom = dfolder.dataroom
    organization = dataroom.organization
    root_folder = Folder.objects.get_root_for_org(organization)
    if not root_folder:
        return

    # Traverse up visual parent hierarchy
    names = []
    curr = dfolder.parent
    while curr:
        names.insert(0, curr.name)
        curr = curr.parent

    if dataroom.vault_folder:
        curr_folder = dataroom.vault_folder
        path_names = names + [old_name]
    else:
        path_names = ["__datarooms__", str(dataroom.id)] + names + [old_name]
        curr_folder = root_folder

    for name in path_names:
        curr_folder = Folder.objects.filter(
            organization=organization,
            parent=curr_folder,
            name=name
        ).first()
        if not curr_folder:
            break

    if curr_folder:
        curr_folder.name = new_name
        curr_folder.save(update_fields=['name'])


def sync_dataroom_document_rename(ddoc: DataroomDocument, new_name: str):
    """
    Synchronizes the backing physical Document name when a visual DataroomDocument is renamed,
    if the document was a direct upload in a Dataroom storage vault.
    Guards against IntegrityError from unique_active_document_name constraint.
    """
    if not new_name or not ddoc.document:
        return

    if is_direct_upload_dataroom_document(ddoc):
        doc = ddoc.document
        target_name = _get_unique_document_name(doc.created_by, doc.folder, new_name)
        if doc.name != target_name:
            doc.name = target_name
            try:
                doc.save(update_fields=['name'])
            except Exception as e:
                logger.warning("Failed to rename physical backing document %s: %s", doc.id, e)


def get_dataroom_storage_used_bytes(dataroom: Dataroom) -> int:
    """
    Calculates total storage used by unique active documents in a dataroom.
    Guarantees that a document linked into multiple folders is counted only once.
    """
    doc_ids = DataroomDocument.objects.filter(
        dataroom=dataroom
    ).values_list('document_id', flat=True)

    return Document.objects.filter(
        id__in=doc_ids,
        deleted_at__isnull=True
    ).aggregate(total=Sum('file_size'))['total'] or 0

