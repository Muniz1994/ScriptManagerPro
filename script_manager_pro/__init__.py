bl_info = {
    "name": "Script Manager Pro",
    "blender": (4, 4, 0),
    "version": (0, 1, 0),
    "category": "Development",
    "author": "Cemil Berk",
    "description": "Manage and run your Python scripts in Blender with one-click access, favorites, tags, editable parameters, and in-panel editing.",
    "support": "COMMUNITY"
}

import bpy
import os
import json
import struct
from bpy.props import (
    StringProperty,
    CollectionProperty,
    BoolProperty,
    IntProperty,
    FloatProperty,
)
from bpy.types import Operator, Panel, PropertyGroup, AddonPreferences

from . import script_parameters

if "_reload_guard" in locals():
    import importlib
    script_parameters = importlib.reload(script_parameters)
_reload_guard = True


# Blender's IntProperty is 32 bit; values outside this range cannot be edited.
_INT_MIN = -2 ** 31
_INT_MAX = 2 ** 31 - 1

# Which typed field on ScriptParameter holds the value for a given type.
PARAM_VALUE_PROP = {
    script_parameters.TYPE_BOOL: "val_bool",
    script_parameters.TYPE_INT: "val_int",
    script_parameters.TYPE_FLOAT: "val_float",
    script_parameters.TYPE_STRING: "val_string",
}

PARAM_DEFAULT_PROP = {
    script_parameters.TYPE_BOOL: "def_bool",
    script_parameters.TYPE_INT: "def_int",
    script_parameters.TYPE_FLOAT: "def_float",
    script_parameters.TYPE_STRING: "def_string",
}


def get_metadata_path(script_dir):
    return os.path.join(script_dir, "Manager_preferences", "preferences.json")

def list_scripts(script_dir):
    return [f for f in os.listdir(script_dir) if f.endswith(".py")]

def load_metadata(script_dir):
    metadata_path = get_metadata_path(script_dir)
    if os.path.exists(metadata_path):
        try:
            with open(metadata_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except (OSError, ValueError):
            return {}
        if isinstance(data, dict):
            return data
    return {}

def save_metadata(script_dir, data):
    metadata_path = get_metadata_path(script_dir)
    os.makedirs(os.path.dirname(metadata_path), exist_ok=True)
    with open(metadata_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4)

def get_prefs(context):
    addon = context.preferences.addons.get(__package__)
    return addon.preferences if addon else None

def metadata_entry(metadata, name):
    """Return the (dict) metadata entry for a script, creating it if needed."""
    entry = metadata.get(name)
    if not isinstance(entry, dict):
        entry = {}
        metadata[name] = entry
    return entry

def store_item_metadata(metadata, item):
    """Write an item's tags/favorite/name without dropping its parameters."""
    entry = metadata_entry(metadata, item.name)
    entry["favorite"] = item.favorite
    entry["tags"] = item.tags
    entry["custom_display_name"] = item.custom_display_name
    return entry

def saved_parameters(metadata, name):
    entry = metadata.get(name)
    if isinstance(entry, dict):
        stored = entry.get("parameters")
        if isinstance(stored, dict):
            return stored
    return {}


# ---------------------------------------------------------------------------
# Parameter helpers
# ---------------------------------------------------------------------------

def _as_float32(value):
    """Round-trip through a C float, matching how Blender stores FloatProperty."""
    try:
        return struct.unpack("f", struct.pack("f", float(value)))[0]
    except (OverflowError, ValueError):
        return float(value)

def set_param_value(param, value, also_default=False):
    prop = PARAM_VALUE_PROP.get(param.param_type)
    if prop is None:
        return
    setattr(param, prop, value)
    if also_default:
        setattr(param, PARAM_DEFAULT_PROP[param.param_type], value)

def get_param_value(param):
    prop = PARAM_VALUE_PROP.get(param.param_type)
    if prop is None:
        return None
    value = getattr(param, prop)
    if param.param_type == script_parameters.TYPE_FLOAT:
        # FloatProperty is single precision, so trim the noise digits that a
        # float32 round-trip introduces before writing the value back out.
        return float(f"{value:.7g}")
    return value

def is_param_modified(param):
    prop = PARAM_VALUE_PROP.get(param.param_type)
    if prop is None:
        return False
    return getattr(param, prop) != getattr(param, PARAM_DEFAULT_PROP[param.param_type])

def reset_param(param):
    prop = PARAM_VALUE_PROP.get(param.param_type)
    if prop is not None:
        setattr(param, prop, getattr(param, PARAM_DEFAULT_PROP[param.param_type]))

def collect_overrides(item):
    """Values that differ from the ones written in the script file."""
    return {
        p.name: get_param_value(p)
        for p in item.parameters
        if p.is_valid and is_param_modified(p)
    }

def scan_item_parameters(item, saved=None):
    """Re-read a script from disk and rebuild its parameter list.

    ``saved`` holds persisted overrides which are re-applied when they still
    match the parameter's type. Returns ``True`` if the file could be read.
    """
    item.parameters.clear()
    item.param_error = ""
    saved = saved or {}

    try:
        source = script_parameters.read_source(item.path)
    except (OSError, UnicodeDecodeError) as exc:
        item.param_error = f"Could not read script: {exc}"
        return False

    parsed_params, errors = script_parameters.parse_exported_parameters(source)
    item.param_error = "\n".join(errors)

    for parsed in parsed_params:
        param = item.parameters.add()
        param.name = parsed.name
        param.description = parsed.description
        param.line = parsed.line

        if not parsed.valid:
            param.is_valid = False
            param.error = parsed.error
            continue

        if parsed.type == script_parameters.TYPE_INT and not (_INT_MIN <= parsed.value <= _INT_MAX):
            param.is_valid = False
            param.error = "value is outside the editable 32 bit integer range"
            continue

        param.param_type = parsed.type
        set_param_value(param, parsed.value, also_default=True)

        if parsed.name in saved:
            override = script_parameters.coerce_value(parsed.type, saved[parsed.name])
            if override is None:
                continue
            if parsed.type == script_parameters.TYPE_INT and not (_INT_MIN <= override <= _INT_MAX):
                continue
            set_param_value(param, override)

    return True

# ---------------------------------------------------------------------------
# Text editor helpers
# ---------------------------------------------------------------------------

def _normalised(path):
    try:
        return os.path.normcase(os.path.abspath(bpy.path.abspath(path)))
    except Exception:
        return ""

def load_text_datablock(path):
    """Return a text datablock for ``path``, reusing one already loaded.

    ``bpy.data.texts.load`` happily loads the same file twice, so without this
    every click would pile up 'script.py.001', 'script.py.002', ...
    """
    target = _normalised(path)
    if target:
        for text in bpy.data.texts:
            if text.filepath and _normalised(text.filepath) == target:
                return text
    try:
        return bpy.data.texts.load(path)
    except RuntimeError:
        return bpy.data.texts.get(os.path.basename(path))

def iter_text_editor_areas(context):
    """Yield ``(area, workspace)`` for every Text Editor, nearest first.

    ``workspace`` is ``None`` when the area is already on screen; otherwise it
    is the workspace the caller has to switch to for the area to be visible.
    """
    windows = list(context.window_manager.windows)
    current = getattr(context, "window", None)
    if current is not None and current in windows:
        windows.remove(current)
        windows.insert(0, current)

    visible_screens = []
    for window in windows:
        visible_screens.append(window.screen)
        for area in window.screen.areas:
            if area.type == 'TEXT_EDITOR':
                yield area, None

    for workspace in bpy.data.workspaces:
        for screen in workspace.screens:
            if screen in visible_screens:
                continue
            for area in screen.areas:
                if area.type == 'TEXT_EDITOR':
                    yield area, workspace

def show_text_in_editor(context, text):
    """Show ``text`` in an existing Text Editor. Returns where, or ``None``."""
    for area, workspace in iter_text_editor_areas(context):
        space = area.spaces.active
        if space is None:
            continue
        space.text = text
        if workspace is None:
            return "Text Editor"
        try:
            context.window.workspace = workspace
        except Exception:
            pass
        return f"'{workspace.name}' workspace"
    return None

def open_text_editor_window(context, text):
    """Last resort: open a new window and turn it into a Text Editor."""
    wm = context.window_manager
    before = list(wm.windows)
    before_screens = [window.screen for window in before]
    try:
        bpy.ops.wm.window_new()
    except (RuntimeError, AttributeError):
        return None

    new_windows = [window for window in wm.windows if window not in before]
    if not new_windows:
        return None
    # A duplicated window that shares its layout would drag the original along
    # with it, so only repurpose a screen of its own.
    if new_windows[0].screen in before_screens:
        return None

    areas = [
        area for area in new_windows[0].screen.areas
        if area.type not in {'TOPBAR', 'STATUSBAR'}
    ]
    if not areas:
        return None
    area = max(areas, key=lambda a: a.width * a.height)
    area.type = 'TEXT_EDITOR'
    space = area.spaces.active
    if space is None:
        return None
    space.text = text
    return "new window"


def find_item(context, script_name=None, path=None):
    for item in context.window_manager.script_list:
        if script_name is not None and item.name == script_name:
            return item
        if path is not None and item.path == path:
            return item
    return None


class ScriptManagerPreferences(AddonPreferences):
    bl_idname = __package__

    script_dir: StringProperty(
        name="Scripts Folder Path",
        subtype='DIR_PATH',
        default=""
    )

    enable_parameters: BoolProperty(
        name="Editable Script Parameters",
        description=(
            "Show an editable parameter section under each script for module "
            "level variables marked with an '# @PARAM' comment. "
            "Values are applied when the script runs; the script file itself "
            "is never modified"
        ),
        default=True
    )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "script_dir")

        box = layout.box()
        box.prop(self, "enable_parameters")
        col = box.column(align=True)
        col.active = self.enable_parameters
        col.label(text="Mark a variable in your script to expose it in the sidebar:", icon='INFO')
        col.label(text="    # @PARAM")
        col.label(text="    copies = 3")
        col.label(text="Only str, int, float and bool values can be exported.")


class ScriptParameter(PropertyGroup):
    name: StringProperty()
    param_type: StringProperty(default='')
    description: StringProperty(default='')
    line: IntProperty(default=0)
    is_valid: BoolProperty(default=True)
    error: StringProperty(default='')

    val_bool: BoolProperty(default=False)
    val_int: IntProperty(default=0)
    val_float: FloatProperty(default=0.0, precision=6)
    val_string: StringProperty(default='')

    def_bool: BoolProperty(default=False)
    def_int: IntProperty(default=0)
    def_float: FloatProperty(default=0.0, precision=6)
    def_string: StringProperty(default='')


class ScriptItem(PropertyGroup):
    name: StringProperty()
    path: StringProperty()
    favorite: BoolProperty(default=False)
    tags: StringProperty(default='')
    custom_display_name: StringProperty(default='')
    edit_mode: BoolProperty(default=False)
    show_parameters: BoolProperty(default=False)
    parameters: CollectionProperty(type=ScriptParameter)
    param_error: StringProperty(default='')

class SCRIPT_OT_RunScript(Operator):
    bl_idname = "script_manager.run_script"
    bl_label = "Run Script"
    bl_description = "Run the selected Python script."

    path: StringProperty()

    def execute(self, context):
        filename = os.path.basename(self.path)
        prefs = get_prefs(context)

        patched_source = None
        if prefs and prefs.enable_parameters:
            item = find_item(context, path=self.path)
            if item is not None:
                overrides = collect_overrides(item)
                if overrides:
                    try:
                        original = script_parameters.read_source(self.path)
                    except (OSError, UnicodeDecodeError) as exc:
                        self.report({'ERROR'}, f"Could not read {filename}: {exc}")
                        return {'CANCELLED'}
                    candidate, applied = script_parameters.apply_overrides(original, overrides)
                    if applied:
                        patched_source = candidate

        text = None
        created = False
        try:
            text = bpy.data.texts.load(self.path)
            created = True
        except RuntimeError:
            text = bpy.data.texts.get(filename)

        if not text:
            self.report({'ERROR'}, f"Failed to load script: {filename}")
            return {'CANCELLED'}

        if patched_source is not None:
            if created:
                text.clear()
            else:
                # Reusing a datablock the user already had open; run the
                # patched source from a throwaway copy instead of overwriting
                # theirs.
                text = bpy.data.texts.new(filename)
                created = True
                try:
                    text.filepath = self.path
                except Exception:
                    pass
            text.write(patched_source)

        area = context.area
        old_type = area.type

        try:
            area.type = 'TEXT_EDITOR'
            area.spaces.active.text = text
            bpy.ops.text.run_script()
            self.report({'INFO'}, f"Executed: {filename}")
        except Exception as e:
            self.report({'ERROR'}, f"Execution error: {e}")
        finally:
            area.type = old_type
            if created:
                bpy.data.texts.remove(text)

        return {'FINISHED'}

class SCRIPT_OT_OpenScript(Operator):
    bl_idname = "script_manager.open_script"
    bl_label = "Open in Text Editor"
    bl_description = "Open the script in Blender's text editor."

    path: StringProperty()

    def execute(self, context):
        filename = os.path.basename(self.path)
        text = load_text_datablock(self.path)
        if text is None:
            self.report({'ERROR'}, f"Failed to load script: {filename}")
            return {'CANCELLED'}

        # Nearest first: this window, another window, another workspace, and
        # only then a brand new window.
        where = show_text_in_editor(context, text) or open_text_editor_window(context, text)
        if where is None:
            self.report(
                {'WARNING'},
                f"No Text Editor available. Switch an editor to Text Editor - "
                f"'{text.name}' is loaded and ready to pick from its list.",
            )
            return {'FINISHED'}

        self.report({'INFO'}, f"Opened {filename} in the {where}")
        return {'FINISHED'}

class SCRIPT_OT_ToggleFavorite(Operator):
    bl_idname = "script_manager.toggle_favorite"
    bl_label = "Toggle Favorite"
    bl_description = "Add or remove this script from favorites."

    script_name: StringProperty()

    def execute(self, context):
        wm = context.window_manager
        script_dir = get_prefs(context).script_dir
        metadata = load_metadata(script_dir)
        for item in wm.script_list:
            if item.name == self.script_name:
                item.favorite = not item.favorite
                store_item_metadata(metadata, item)
                state = "added to" if item.favorite else "removed from"
                self.report({'INFO'}, f"{item.name} {state} favorites.")
                break
        save_metadata(script_dir, metadata)
        return {'FINISHED'}

class SCRIPT_OT_ToggleEditMode(Operator):
    bl_idname = "script_manager.toggle_edit_mode"
    bl_label = "Toggle Edit Mode"
    bl_description = "Toggle metadata editing for this script."

    script_name: StringProperty()

    def execute(self, context):
        wm = context.window_manager
        for item in wm.script_list:
            if item.name == self.script_name:
                item.edit_mode = not item.edit_mode
                mode = "enabled" if item.edit_mode else "disabled"
                self.report({'INFO'}, f"Edit mode {mode} for {item.name}")
                break
        return {'FINISHED'}

class SCRIPT_OT_SaveMetadata(Operator):
    bl_idname = "script_manager.save_metadata"
    bl_label = "Save Metadata"
    bl_description = "Save tags and display name for this script."

    script_name: StringProperty()

    def execute(self, context):
        wm = context.window_manager
        script_dir = get_prefs(context).script_dir
        metadata = load_metadata(script_dir)
        for item in wm.script_list:
            if item.name == self.script_name:
                store_item_metadata(metadata, item)
                item.edit_mode = False
                self.report({'INFO'}, f"Metadata saved for {item.name}")
                break
        save_metadata(script_dir, metadata)
        return {'FINISHED'}


class SCRIPT_OT_ToggleParameters(Operator):
    bl_idname = "script_manager.toggle_parameters"
    bl_label = "Toggle Parameters"
    bl_description = "Show or hide the editable @PARAM parameters of this script."

    script_name: StringProperty()

    def execute(self, context):
        item = find_item(context, script_name=self.script_name)
        if item is None:
            return {'CANCELLED'}

        item.show_parameters = not item.show_parameters
        if item.show_parameters:
            # Re-read on open so edits made to the file since the last refresh
            # are picked up.
            script_dir = get_prefs(context).script_dir
            metadata = load_metadata(script_dir)
            scan_item_parameters(item, saved_parameters(metadata, item.name))
        return {'FINISHED'}


class SCRIPT_OT_ScanParameters(Operator):
    bl_idname = "script_manager.scan_parameters"
    bl_label = "Reload Parameters"
    bl_description = "Re-read this script from disk and rebuild its parameter list."

    script_name: StringProperty()

    def execute(self, context):
        item = find_item(context, script_name=self.script_name)
        if item is None:
            return {'CANCELLED'}

        script_dir = get_prefs(context).script_dir
        metadata = load_metadata(script_dir)
        if not scan_item_parameters(item, saved_parameters(metadata, item.name)):
            self.report({'ERROR'}, item.param_error)
            return {'CANCELLED'}

        invalid = sum(1 for p in item.parameters if not p.is_valid)
        valid = len(item.parameters) - invalid
        message = f"{valid} parameter(s) found in {item.name}"
        if invalid or item.param_error:
            self.report({'WARNING'}, f"{message}; see the panel for problems.")
        else:
            self.report({'INFO'}, message)
        return {'FINISHED'}


class SCRIPT_OT_ResetParameters(Operator):
    bl_idname = "script_manager.reset_parameters"
    bl_label = "Reset Parameters"
    bl_description = "Restore the value(s) written in the script file."

    script_name: StringProperty()
    param_name: StringProperty()

    def execute(self, context):
        item = find_item(context, script_name=self.script_name)
        if item is None:
            return {'CANCELLED'}

        for param in item.parameters:
            if not param.is_valid:
                continue
            if self.param_name and param.name != self.param_name:
                continue
            reset_param(param)

        target = self.param_name or item.name
        self.report({'INFO'}, f"Reset {target} to the script defaults.")
        return {'FINISHED'}


class SCRIPT_OT_SaveParameters(Operator):
    bl_idname = "script_manager.save_parameters"
    bl_label = "Save Parameters"
    bl_description = "Remember these parameter values across Blender sessions."

    script_name: StringProperty()

    def execute(self, context):
        item = find_item(context, script_name=self.script_name)
        if item is None:
            return {'CANCELLED'}

        script_dir = get_prefs(context).script_dir
        metadata = load_metadata(script_dir)
        entry = metadata_entry(metadata, item.name)
        overrides = collect_overrides(item)
        if overrides:
            entry["parameters"] = overrides
        else:
            entry.pop("parameters", None)
        save_metadata(script_dir, metadata)

        self.report({'INFO'}, f"Saved {len(overrides)} parameter override(s) for {item.name}")
        return {'FINISHED'}


class SCRIPT_OT_RefreshList(Operator):
    bl_idname = "script_manager.refresh_list"
    bl_label = "Refresh List"
    bl_description = "Refresh the list of available scripts."

    def execute(self, context):
        wm = context.window_manager
        prefs = get_prefs(context)
        script_dir = prefs.script_dir
        if not os.path.isdir(script_dir):
            self.report({'ERROR'}, "Invalid Scripts Folder Path in Addon Preferences.")
            return {'CANCELLED'}

        wm.script_list.clear()
        metadata = load_metadata(script_dir)
        for fname in list_scripts(script_dir):
            entry = metadata.get(fname)
            if not isinstance(entry, dict):
                entry = {}
            item = wm.script_list.add()
            item.name = fname
            item.path = os.path.join(script_dir, fname)
            item.favorite = entry.get("favorite", False)
            item.tags = entry.get("tags", "")
            item.custom_display_name = entry.get("custom_display_name", "")
            if prefs.enable_parameters:
                scan_item_parameters(item, saved_parameters(metadata, fname))
        self.report({'INFO'}, "Script list refreshed.")
        return {'FINISHED'}

class SCRIPT_OT_OpenScriptFolder(Operator):
    bl_idname = "script_manager.open_script_folder"
    bl_label = "Open Scripts Folder"
    bl_description = "Open the scripts folder in your file explorer."

    def execute(self, context):
        script_dir = get_prefs(context).script_dir
        if os.path.isdir(script_dir):
            os.startfile(script_dir)
            self.report({'INFO'}, "Scripts folder opened.")
        else:
            self.report({'ERROR'}, "Invalid Scripts Folder Path.")
        return {'FINISHED'}

class SCRIPT_PT_ScriptManagerPanel(Panel):
    bl_label = "Script Manager"
    bl_idname = "SCRIPT_PT_script_manager"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Script Manager'

    def draw_parameters(self, layout, item):
        box = layout.box()

        header = box.row(align=True)
        header.label(text="Parameters", icon='PRESET')
        reload_op = header.operator("script_manager.scan_parameters", text="", icon='FILE_REFRESH')
        reload_op.script_name = item.name
        reset_op = header.operator("script_manager.reset_parameters", text="", icon='LOOP_BACK')
        reset_op.script_name = item.name
        reset_op.param_name = ""
        save_op = header.operator("script_manager.save_parameters", text="", icon='CHECKMARK')
        save_op.script_name = item.name

        for line in item.param_error.split("\n"):
            if line:
                row = box.row()
                row.alert = True
                row.label(text=line, icon='ERROR')

        if not len(item.parameters):
            box.label(text="No @PARAM parameters in this script.", icon='INFO')
            return

        col = box.column()
        for param in item.parameters:
            if not param.is_valid:
                row = col.row()
                row.alert = True
                row.label(text=f"{param.name} (line {param.line}): {param.error}", icon='ERROR')
                continue

            row = col.row(align=True)
            row.prop(param, PARAM_VALUE_PROP[param.param_type], text=param.name)
            if is_param_modified(param):
                single = row.operator("script_manager.reset_parameters", text="", icon='LOOP_BACK')
                single.script_name = item.name
                single.param_name = param.name
            else:
                row.label(text="", icon='BLANK1')

            if param.description:
                sub = col.row()
                sub.active = False
                sub.label(text=param.description)

    def draw(self, context):
        layout = self.layout
        wm = context.window_manager
        prefs = get_prefs(context)
        parameters_enabled = bool(prefs and prefs.enable_parameters)

        row = layout.row()
        row.operator("script_manager.refresh_list", icon='FILE_REFRESH', text="Refresh List")
        row.operator("script_manager.open_script_folder", icon='FILE_FOLDER', text="Open Scripts Folder")

        row = layout.row()
        row.prop(wm, "filter_tags", text="Tags")
        row.prop(wm, "show_favorites_only", toggle=True, text="Favorites", icon='SOLO_ON')

        for item in wm.script_list:
            if wm.show_favorites_only and not item.favorite:
                continue
            if wm.filter_tags.strip() and wm.filter_tags.lower() not in item.tags.lower():
                continue
            box = layout.box()
            row = box.row()
            display_name = item.custom_display_name if item.custom_display_name.strip() else item.name
            row.label(text=display_name, icon='SCRIPT')
            run = row.operator("script_manager.run_script", text="", icon='PLAY')
            run.path = item.path
            edit = row.operator("script_manager.open_script", text="", icon='TEXT')
            edit.path = item.path
            fav = row.operator("script_manager.toggle_favorite", text="", icon='SOLO_ON' if item.favorite else 'SOLO_OFF')
            fav.script_name = item.name
            if parameters_enabled:
                params = row.operator(
                    "script_manager.toggle_parameters",
                    text="",
                    icon='PRESET',
                    depress=item.show_parameters,
                )
                params.script_name = item.name
            toggle = row.operator("script_manager.toggle_edit_mode", text="", icon='GREASEPENCIL')
            toggle.script_name = item.name

            if item.edit_mode:
                box.prop(item, "custom_display_name", text="Display Name")
                box.prop(item, "tags", text="Tags")
                save = box.operator("script_manager.save_metadata", text="Save")
                save.script_name = item.name
            else:
                if item.tags.strip():
                    row = box.row()
                    row.label(text=f"Tags: {item.tags}")

            if parameters_enabled and item.show_parameters:
                self.draw_parameters(box, item)

classes = (
    ScriptManagerPreferences,
    ScriptParameter,
    ScriptItem,
    SCRIPT_OT_RunScript,
    SCRIPT_OT_OpenScript,
    SCRIPT_OT_ToggleFavorite,
    SCRIPT_OT_ToggleEditMode,
    SCRIPT_OT_SaveMetadata,
    SCRIPT_OT_ToggleParameters,
    SCRIPT_OT_ScanParameters,
    SCRIPT_OT_ResetParameters,
    SCRIPT_OT_SaveParameters,
    SCRIPT_OT_RefreshList,
    SCRIPT_OT_OpenScriptFolder,
    SCRIPT_PT_ScriptManagerPanel,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.script_list = CollectionProperty(type=ScriptItem)
    bpy.types.WindowManager.show_favorites_only = BoolProperty(name="Favorites Only", default=False)
    bpy.types.WindowManager.filter_tags = StringProperty(name="Tag Filter", default="")

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.WindowManager.script_list
    del bpy.types.WindowManager.show_favorites_only
    del bpy.types.WindowManager.filter_tags

if __name__ == "__main__":
    register()
