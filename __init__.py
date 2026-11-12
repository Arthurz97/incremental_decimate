import math
import bpy
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)
from bpy.types import Operator, Panel, PropertyGroup


def get_mesh_triangle_count(obj):
    """Calculate the actual triangle count of the active mesh."""
    mesh = obj.data
    mesh.calc_loop_triangles()
    return len(mesh.loop_triangles)


_STEP_RATIO_MIN = 0.01
_STEP_RATIO_MAX = 0.9999


def _get_step_ratio(self):
    return self.step_ratio_internal


def _set_step_ratio(self, value):
    self.step_ratio_internal = min(max(value, _STEP_RATIO_MIN), _STEP_RATIO_MAX)


class IncrementalDecimateProperties(PropertyGroup):
    target_triangles: IntProperty(
        name="Target Triangles",
        description="Desired final triangle count",
        default=1000,
        min=1,
    )

    use_triangulate: BoolProperty(
        name="Triangulate",
        description="Ensures the mesh is triangulated during decimation",
        default=True,
    )

    use_symmetry: BoolProperty(
        name="Symmetry",
        description="Maintains symmetry along the specified axis",
        default=False,
    )

    symmetry_axis: EnumProperty(
        name="Symmetry Axis",
        description="Axis along which to keep symmetry",
        items=[
            ("X", "X", "X Axis"),
            ("Y", "Y", "Y Axis"),
            ("Z", "Z", "Z Axis"),
        ],
        default="X",
    )

    vertex_group: StringProperty(
        name="Vertex Group",
        description="Vertex group used to influence the decimation",
        default="",
    )

    invert_vertex_group: BoolProperty(
        name="Invert Group",
        description="Inverts the vertex group weights",
        default=False,
    )

    vertex_group_factor: FloatProperty(
        name="Factor",
        description="Vertex group weight factor",
        default=1.0,
        min=0.0,
        max=1000.0,
        soft_min=0.0,
        soft_max=10.0,
        precision=4,
        subtype="FACTOR",
    )

    mode: EnumProperty(
        name="Increment Mode",
        description="Choose the strategy for computing the passes",
        items=[
            (
                "FIXED_RATIO",
                "Fixed Ratio",
                "Set the ratio per step and compute the number of passes",
            ),
            (
                "FIXED_STEPS",
                "Number of Steps",
                "Set the total number of passes and compute the ratio per step",
            ),
        ],
        default="FIXED_RATIO",
    )

    step_ratio_internal: FloatProperty(
        name="Step Ratio (Internal)",
        description="Internal storage for the step ratio, kept within safe limits",
        default=0.9000,
        min=0.0100,
        max=0.9999,
        precision=4,
        options={"HIDDEN"},
    )

    step_ratio: FloatProperty(
        name="Step Ratio",
        description="Ratio used on each pass up to the final adjusted one",
        default=0.9000,
        min=0.0,
        max=1.0,
        soft_min=0.0,
        soft_max=1.0,
        precision=4,
        step=1,
        subtype="FACTOR",
        get=_get_step_ratio,
        set=_set_step_ratio,
    )

    step_count: IntProperty(
        name="Step Count",
        description="Total number of decimation passes",
        default=10,
        min=1,
        max=500,
    )

    stack_before_apply: BoolProperty(
        name="Stack Modifiers",
        description=(
            "If enabled, adds all intermediate passes to the stack before applying"
            " them. If disabled, applies them one at a time."
        ),
        default=True,
    )


class OBJECT_OT_incremental_decimate_apply(Operator):
    bl_idname = "object.incremental_decimate_apply"
    bl_label = "Apply Incremental Decimate"
    bl_description = (
        "Computes and applies Decimate modifiers incrementally until the exact"
        " target is reached"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and context.active_object.type == "MESH"

    def execute(self, context):
        obj = context.active_object
        props = context.scene.incremental_decimate_props

        # 1. Ensure Object Mode and stay in it
        if obj.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")

        t_0 = get_mesh_triangle_count(obj)
        t_target = props.target_triangles

        # Target validation
        if t_target >= t_0:
            self.report(
                {"WARNING"},
                f"The target triangle count ({t_target:,}) is greater than or equal to the current count ({t_0:,}).",
            )
            return {"CANCELLED"}

        # 2. Compute the number of intermediate passes and the base ratio
        if props.mode == "FIXED_RATIO":
            r_step = props.step_ratio
            ratio_total = t_target / t_0
            k = math.floor(math.log(ratio_total) / math.log(r_step))
            num_intermediate_passes = max(0, k)
        else:  # FIXED_STEPS
            n = props.step_count
            if n <= 1:
                num_intermediate_passes = 0
                r_step = t_target / t_0
            else:
                num_intermediate_passes = n - 1
                r_step = (t_target / t_0) ** (1.0 / n)

        def setup_modifier_props(mod, ratio_val):
            """Set up the Decimate modifier properties in Collapse mode."""
            mod.decimate_type = "COLLAPSE"
            mod.ratio = max(min(ratio_val, 1.0), 0.0001)
            mod.use_collapse_triangulate = props.use_triangulate
            mod.use_symmetry = props.use_symmetry
            mod.symmetry_axis = props.symmetry_axis
            if props.vertex_group and props.vertex_group in obj.vertex_groups:
                mod.vertex_group = props.vertex_group
                mod.invert_vertex_group = props.invert_vertex_group
                mod.vertex_group_factor = props.vertex_group_factor

        # 3. Run the intermediate passes
        if num_intermediate_passes > 0:
            if props.stack_before_apply:
                # Case A: Stack in batch and apply all at once
                mod_names = []
                for i in range(num_intermediate_passes):
                    mod = obj.modifiers.new(
                        name=f"IncDecimate_Pass_{i+1}", type="DECIMATE"
                    )
                    setup_modifier_props(mod, r_step)
                    mod_names.append(mod.name)

                for m_name in mod_names:
                    bpy.ops.object.modifier_apply(modifier=m_name)
            else:
                # Case B: Apply one by one sequentially
                for i in range(num_intermediate_passes):
                    mod = obj.modifiers.new(
                        name=f"IncDecimate_Pass_{i+1}", type="DECIMATE"
                    )
                    setup_modifier_props(mod, r_step)
                    bpy.ops.object.modifier_apply(modifier=mod.name)

        # 4. Real measurement and final pass ratio computation
        t_current = get_mesh_triangle_count(obj)

        if t_current > t_target:
            r_final = t_target / t_current
            final_mod = obj.modifiers.new(name="IncDecimate_Final", type="DECIMATE")
            setup_modifier_props(final_mod, r_final)
            bpy.ops.object.modifier_apply(modifier=final_mod.name)

        t_final = get_mesh_triangle_count(obj)
        self.report(
            {"INFO"},
            f"Decimation complete: {t_0:,} → {t_final:,} triangles (Target: {t_target:,}).",
        )
        return {"FINISHED"}


class OBJECT_PT_incremental_decimate_panel(Panel):
    bl_label = "Incremental Decimate"
    bl_idname = "OBJECT_PT_incremental_decimate_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Incremental Decimate"

    def draw(self, context):
        layout = self.layout
        obj = context.active_object
        props = context.scene.incremental_decimate_props

        # Mesh info panel
        box_info = layout.box()
        if obj and obj.type == "MESH":
            t_current = get_mesh_triangle_count(obj)
            box_info.label(
                text=f"Current Triangles: {t_current:,}", icon="MESH_DATA"
            )
        else:
            box_info.label(
                text="Select a Mesh object", icon="INFO"
            )
            return

        # Decimate section — mirrors the native Decimate modifier panel
        layout.use_property_split = True
        layout.use_property_decorate = False

        layout.prop(props, "target_triangles", text="Target Triangles")

        # Symmetry: heading in the split label column, checkbox + axes inline
        row_sym = layout.row(align=True, heading="Symmetry")
        row_sym.use_property_decorate = False
        sub = row_sym.row(align=True)
        sub.prop(props, "use_symmetry", text="")
        sub = sub.row(align=True)
        sub.enabled = props.use_symmetry
        sub.prop(props, "symmetry_axis", expand=True)

        layout.prop(props, "use_triangulate", text="Triangulate")

        # Vertex Group: search field + invert button on the same row,
        # Factor below (disabled when no group), like the native modifier
        row_vg = layout.row(align=True)
        row_vg.prop_search(
            props,
            "vertex_group",
            obj,
            "vertex_groups",
            text="Vertex Group",
            icon="GROUP_VERTEX",
        )
        sub_vg = row_vg.row(align=True)
        sub_vg.enabled = bool(props.vertex_group)
        sub_vg.use_property_decorate = False
        sub_vg.prop(
            props, "invert_vertex_group", text="", icon="ARROW_LEFTRIGHT"
        )

        row_factor = layout.row(align=True)
        row_factor.enabled = bool(props.vertex_group)
        row_factor.prop(props, "vertex_group_factor", text="Factor")

        layout.separator()

        # Increment settings (add-on specific, kept in its own box)
        box_inc = layout.box()
        box_inc.use_property_split = True
        box_inc.use_property_decorate = False
        box_inc.label(text="Increment Settings", icon="MOD_DECIM")

        box_inc.prop(props, "mode", text="Mode")
        if props.mode == "FIXED_RATIO":
            box_inc.prop(props, "step_ratio", text="Step Ratio", slider=True)
        else:
            box_inc.prop(props, "step_count", text="Step Count")
        box_inc.prop(props, "stack_before_apply", text="Stack in Batch")

        layout.separator()

        # Execute button
        btn_col = layout.column()
        btn_col.scale_y = 1.4
        btn_col.operator(
            "object.incremental_decimate_apply",
            text="Apply Decimate",
            icon="PLAY",
        )


classes = (
    IncrementalDecimateProperties,
    OBJECT_OT_incremental_decimate_apply,
    OBJECT_PT_incremental_decimate_panel,
)


@bpy.app.handlers.persistent
def _migrate_legacy_step_ratio(*_args):
    """Move a pre-callback step_ratio value into the backing property."""
    for scene in bpy.data.scenes:
        props = scene.incremental_decimate_props
        legacy_value = props.get("step_ratio")
        if isinstance(legacy_value, float):
            props.step_ratio = legacy_value
            del props["step_ratio"]


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.incremental_decimate_props = PointerProperty(
        type=IncrementalDecimateProperties
    )
    _migrate_legacy_step_ratio()
    bpy.app.handlers.load_post.append(_migrate_legacy_step_ratio)


def unregister():
    if _migrate_legacy_step_ratio in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_migrate_legacy_step_ratio)
    del bpy.types.Scene.incremental_decimate_props
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()