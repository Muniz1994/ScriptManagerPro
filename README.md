# Script Manager Pro

A Blender add-on for managing and running external Python scripts with one-click access, favorites, tags, and in-panel metadata editing.

![Blender](https://img.shields.io/badge/Blender-4.4%2B-orange)
![Version](https://img.shields.io/badge/Version-0.1.0-blue)
![License](https://img.shields.io/badge/License-GPL--3.0-green)

## Features

- **One-Click Script Execution** - Run any Python script directly from the sidebar panel
- **Editable Parameters** - Expose variables with an `# @PARAM` comment and tweak them from the sidebar before running
- **Open in Text Editor** - Quickly open scripts in Blender's built-in text editor
- **Favorites** - Mark frequently used scripts for quick access and filter by favorites
- **Custom Tags** - Add tags to scripts for categorization and filtering
- **Custom Display Names** - Set friendly names for your scripts
- **In-Panel Editing** - Edit script metadata (tags, display name) without leaving the panel
- **Persistent Metadata** - All favorites, tags, display names, and parameter values are saved across sessions
- **Open Scripts Folder** - Quick access to your scripts directory from the panel

## Installation

### As Blender Extension (Recommended)
1. Download the latest release `.zip`
2. In Blender, go to **Edit > Preferences > Get Extensions**
3. Click the dropdown arrow and select **Install from Disk**
4. Select the downloaded `.zip` file

### Manual Installation
1. Clone or download this repository
2. Copy the `script_manager_pro` folder to your Blender extensions directory

## Setup

1. After installation, go to **Edit > Preferences > Add-ons**
2. Find **Script Manager Pro** and expand it
3. Set the **Scripts Folder Path** to the directory containing your Python scripts
4. Open the **3D Viewport** sidebar (press `N`) and find the **Script Manager** tab

## Usage

1. Click **Refresh List** to scan your scripts folder
2. Each script appears with action buttons:
   - **Play** - Execute the script
   - **Text** - Open in Blender's text editor
   - **Star** - Toggle favorite status
   - **Preset** - Show the script's editable parameters (see below)
   - **Pencil** - Edit metadata (display name, tags)

The **Text** button looks for a Text Editor in this order: the current window,
any other open window, any other workspace (switching to it - usually
**Scripting**), and finally a new window. A script that is already loaded is
reused rather than opened again as a `.001` copy.
3. Use the **Tags** filter to find scripts by tag
4. Toggle **Favorites** to show only starred scripts

## Script Parameters

Enable **Editable Script Parameters** in the add-on preferences
(**Edit > Preferences > Add-ons > Script Manager Pro**) to expose selected
variables from your scripts directly in the sidebar. Each script then gets a
**Preset** button that toggles a **Parameters** section underneath it.

### Marking a variable

Put an `# @PARAM` comment directly above a module-level assignment, or at the
end of the line:

```python
# @PARAM: How many copies to create
copies = 3

# @PARAM
prefix = "Cube"

use_modifiers = True  # @PARAM

# @PARAM
scale: float = 1.5
```

The text after `@PARAM:` is shown as a hint under the field. If you omit it,
any plain comment lines between the marker and the assignment are used instead.

### Supported types

Only scalar literals can be exported: **`str`**, **`int`**, **`float`** and
**`bool`**. Anything else is listed in the panel with the reason it was
rejected, rather than being silently skipped:

| Rejected | Reason |
|----------|--------|
| `items = [1, 2]`, `cfg = {"a": 1}` | container types cannot be edited |
| `target = None` | `NoneType` is not an editable type |
| `size = 2 ** 8`, `obj = bpy.context.object` | value is not a literal |
| `a = b = 1`, `x, y = 1, 2` | export one plain variable per statement |
| `count += 1` | augmented assignment has no literal value |
| `ratio: int = 0.5` | annotation does not match the value |
| `# @PARAM` above a `def`/`import`/blank line | marker is not attached to an assignment |

`@PARAM` is only recognised on **module-level** variables — markers inside a
function or class body are reported as unattached. An `@PARAM` string inside a
string literal is never mistaken for a marker.

### How values are applied

Edited values are substituted into an in-memory copy of the script just before
it runs. **Your `.py` file is never modified** — comments, formatting and all
unrelated code are preserved exactly.

| Button | Action |
|--------|--------|
| **Refresh** (in the Parameters header) | Re-read the script from disk and rebuild the list |
| **Loop Back** | Restore the value(s) written in the script file |
| **Checkmark** | Persist the current values to `preferences.json` so they survive a Blender restart |

Only values that differ from the ones in the file are stored and applied. If a
saved value no longer matches the parameter's type (because you edited the
script), it is discarded and the file's value is used.

> **Note:** floats are edited through Blender's single-precision `FloatProperty`,
> so a value you change is written back rounded to 7 significant digits.
> Integers are limited to the signed 32-bit range; larger literals are shown as
> not editable.

## Project Structure

```
script_manager_pro/
├── __init__.py              # Add-on registration, operators, panel and preferences
├── script_parameters.py     # @PARAM discovery, validation and value substitution
├── blender_manifest.toml    # Blender extension manifest
└── README.md
```

## Permissions

| Permission | Reason |
|------------|--------|
| `files` | Reads and writes script metadata to a JSON file |

## Requirements

- Blender 4.4.0 or later
- No external Python dependencies

## License

[GPL-3.0-or-later](https://www.gnu.org/licenses/gpl-3.0.html)

## Author

**Cemil Berk** - [cemilbnr](https://github.com/cemilbnr)
