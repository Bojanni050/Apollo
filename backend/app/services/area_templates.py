"""Starting sets of areas, for a workspace that would rather not invent them.

An area (hoofdgebied) is just a group with a folder and no parent -- nothing
here is a new kind of state. A template is a named list of area names that
:func:`apply_area_template` turns into exactly that, once, through the same
:func:`~app.services.placement.create_group` every other group goes through.
Reviewed immediately (``source="user"``): the reader chose the template, so
there is nothing left for Delphi's accept/reject to ask about.

Applying a template twice, or applying one after some of its areas already
exist (by name, made by hand or by an earlier template), skips the ones that
are already there rather than failing on the first clash -- the point is "make
sure these exist", not "these must not yet exist".
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Group
from app.services.filing import set_group_folder
from app.services.placement import PlacementError, create_group

#: Each value is the starting set of areas (hoofdgebieden) for that kind of
#: project. Names only -- an area is otherwise a plain group, so nothing else
#: about it needs to be templated.
AREA_TEMPLATES: dict[str, list[str]] = {
    "software": ["Architectuur", "Product", "Besluiten", "Onderzoek"],
    "book": ["Hoofdstukken", "Personages", "Wereldopbouw", "Onderzoek", "Drafts"],
    "research": ["Literatuur", "Methode", "Resultaten", "Besluiten"],
}


class UnknownTemplateError(PlacementError):
    """Raised for a template id that is not in the catalog."""


def apply_area_template(db: Session, workspace_id: int, template: str) -> list[Group]:
    """Create the areas this template names, skipping any that already exist.

    Returns every area the template names, whether it was just created or was
    already there -- the caller wants to know the workspace's areas now match
    the template, not which ones this particular call happened to add.
    """
    names = AREA_TEMPLATES.get(template)
    if names is None:
        raise UnknownTemplateError(
            f"Unknown template {template!r}. Known templates: "
            f"{', '.join(sorted(AREA_TEMPLATES))}."
        )

    result: list[Group] = []
    for name in names:
        try:
            group = create_group(db, workspace_id, name, source="user")
            group = set_group_folder(db, workspace_id, group.id, name)
        except PlacementError:
            # Already exists (by name) -- fetch it rather than fail. A template
            # applied a second time, or over areas the reader already made by
            # hand under the same names, still ends with all of them present.
            group = db.scalar(
                select(Group).where(Group.workspace_id == workspace_id, Group.name == name)
            )
            if group is None:
                raise
        result.append(group)
    return result


__all__ = ["AREA_TEMPLATES", "UnknownTemplateError", "apply_area_template"]
