# ILIAS Objects

Analysis of the various object types and their HTML representations in ILIAS.

## Core Object Types

| Type | Description | Command Class (`cmdClass`) | Properties / Identifiers |
| :--- | :--- | :--- | :--- |
| **crs** | Course | `ilobjcoursegui` | `ref_id` |
| **fold** | Folder | `ilobjfoldergui` | |
| **grp** | Group | `ilobjgroupgui` | |
| **file** | File | `ilobjfilegui` | `cmd=sendfile` |

## OpenCast / Podcast Objects

These rely on custom ILIAS plugins:
*   **Plugin Classes**: `ilObjOpenCastGUI`, `xoctEventGUI`, `xoctPlayerGUI`

## Info Screens

*   **Command Class**: `ilInfoScreenGUI`

## HTML Selectors & Scraping

When scraping ILIAS pages, the following CSS selectors are effective:

*   **Items (Links)**: `.il_ContainerItemTitle a[href]`, `.il-item-title a[href]`
*   **Containers**: `div.ilContainerListItemOuter`
*   **Properties** (Dates, Versions, etc.): `span.il_ItemProperty`

## UI Interaction Patterns

*   **Course Tabs**: Courses usually have standard tabs: "Inhalt" (Content), "Info", "Mitglieder" (Members), "Lernfortschritt" (Learning Progress).
*   **Session Expansion**: Sessions can be expanded/collapsed via URL parameters: `crs_next_sess=1`, `crs_prev_sess=1`.
*   **Pagination**: Handled via URLs containing `_trows` or selectors like `.il-pagination a`.
