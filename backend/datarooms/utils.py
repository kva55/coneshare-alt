from datarooms.models import DataroomItemOrder


def build_ordered_dataroom_items(scope_rows, folders_data, documents_data):
    """
    Constructs a unified list of dataroom items with assigned `position` values.
    If scope_rows exist, items are arranged according to DataroomItemOrder,
    with any un-ordered remaining items appended sequentially at the end.
    If no scope_rows exist, items default to folders first, then created_at.
    """
    folder_map = {str(item["id"]): item for item in folders_data}
    doc_map = {str(item["id"]): item for item in documents_data}

    if scope_rows:
        ordered_items = []
        used_keys = set()
        for row in scope_rows:
            if row.item_type == DataroomItemOrder.ITEM_TYPE_FOLDER and row.folder_id and str(row.folder_id) in folder_map:
                key = ('folder', str(row.folder_id))
                ordered_items.append({"type": "folder", **folder_map[str(row.folder_id)], "position": row.position})
                used_keys.add(key)
            elif row.item_type == DataroomItemOrder.ITEM_TYPE_DOCUMENT and row.dataroom_document_id and str(row.dataroom_document_id) in doc_map:
                key = ('document', str(row.dataroom_document_id))
                ordered_items.append({"type": "document", **doc_map[str(row.dataroom_document_id)], "position": row.position})
                used_keys.add(key)

        remaining_folders = [{'type': 'folder', **item} for item in folders_data if ('folder', str(item['id'])) not in used_keys]
        remaining_docs = [{'type': 'document', **item} for item in documents_data if ('document', str(item['id'])) not in used_keys]
        remaining_items = remaining_folders + remaining_docs
        remaining_items.sort(key=lambda i: (i['type'] != 'folder', i.get('created_at', ''), str(i.get('id', ''))))
        next_position = max((i.get('position', 0) for i in ordered_items), default=-1) + 1
        for idx, item in enumerate(remaining_items):
            ordered_items.append({**item, 'position': next_position + idx})
        return ordered_items

    merged = (
        [{'type': 'folder', **item} for item in folders_data] +
        [{'type': 'document', **item} for item in documents_data]
    )
    merged.sort(key=lambda i: (i['type'] != 'folder', i.get('created_at', ''), str(i.get('id', ''))))
    for idx, item in enumerate(merged):
        item['position'] = idx
    return merged


def is_dataroom_owner_or_admin(user, dataroom) -> bool:
    if not user or not user.is_authenticated:
        return False
    if getattr(dataroom, 'created_by_id', None) == user.id:
        return True
    if getattr(user, 'role', '') == 'admin' and getattr(dataroom, 'organization_id', None) == getattr(user, 'organization_id', None):
        return True
    return False


def is_dataroom_collaborator_or_above(user, dataroom) -> bool:
    if is_dataroom_owner_or_admin(user, dataroom):
        return True
    collaborators = getattr(dataroom, 'collaborators', None)
    if collaborators and hasattr(collaborators, 'filter'):
        return collaborators.filter(user=user).exists()
    return False


