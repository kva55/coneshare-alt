"""Apply a target's AccessPolicy to the links that point at it.

Public flows load a link through `_get_active_share_link`, which returns `effective_link(link)`: a
read-only copy whose access fields are the stricter of the link's own and the policy's. Everything
downstream (gates, meta, page delivery) then sees the effective values without knowing about policies.
"""
import copy

from .models import AccessPolicy


def policy_for(link):
    if link.document_id:
        return AccessPolicy.objects.filter(document_id=link.document_id).first()
    if link.dataroom_id:
        return AccessPolicy.objects.filter(dataroom_id=link.dataroom_id).first()
    return None


def effective_link(link):
    policy = policy_for(link)
    if policy is None:
        return link
    eff = copy.copy(link)
    eff._projected = True
    eff._access_policy = policy
    eff.requires_email = bool(link.requires_email or policy.requires_email)
    eff.requires_email_verification = bool(link.requires_email_verification or policy.requires_email_verification)
    eff.password = link.password or policy.password
    if policy.require_nda:
        eff.require_nda = True
        eff.nda_text = policy.nda_text
        eff.nda_version = policy.nda_version
    if policy.allow_download is False:
        eff.allow_download = False
    eff.enable_watermark = bool(link.enable_watermark or policy.enable_watermark)
    if policy.enable_watermark and policy.watermark_text:
        eff.watermark_text = policy.watermark_text
    eff.blur_when_unfocused = bool(link.blur_when_unfocused or policy.blur_when_unfocused)
    eff.restricted_viewing_pane = bool(link.restricted_viewing_pane or policy.restricted_viewing_pane)
    return eff
