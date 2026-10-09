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
    """Calcula a contagem real de triângulos da malha ativa."""
    mesh = obj.data
    mesh.calc_loop_triangles()
    return len(mesh.loop_triangles)


class IncrementalDecimateProperties(PropertyGroup):
    target_triangles: IntProperty(
        name="Target Triangles",
        description="Contagem final de triângulos desejada",
        default=1000,
        min=1,
    )

    use_triangulate: BoolProperty(
        name="Triangulate",
        description="Garante a triangulação da malha durante a dizimação",
        default=True,
    )

    use_symmetry: BoolProperty(
        name="Symmetry",
        description="Mantém a simetria ao longo do eixo especificado",
        default=False,
    )

    symmetry_axis: EnumProperty(
        name="Eixo de Simetria",
        description="Eixo para manter a simetria",
        items=[
            ("X", "X", "Eixo X"),
            ("Y", "Y", "Eixo Y"),
            ("Z", "Z", "Eixo Z"),
        ],
        default="X",
    )

    vertex_group: StringProperty(
        name="Vertex Group",
        description="Grupo de vértices para influenciar a dizimação",
        default="",
    )

    invert_vertex_group: BoolProperty(
        name="Inverter Grupo",
        description="Inverte os pesos do grupo de vértices",
        default=False,
    )

    vertex_group_factor: FloatProperty(
        name="Fator",
        description="Fator de peso do grupo de vértices",
        default=1.0,
        min=0.0,
        max=1.0,
        precision=4,
    )

    mode: EnumProperty(
        name="Modo de Incrementação",
        description="Escolha a estratégia de cálculo das passadas",
        items=[
            (
                "FIXED_RATIO",
                "Ratio Fixo",
                "Define o ratio por passo e calcula a quantidade de passadas",
            ),
            (
                "FIXED_STEPS",
                "Número de Passos",
                "Define o total de passadas e calcula o ratio por passo",
            ),
        ],
        default="FIXED_RATIO",
    )

    step_ratio: FloatProperty(
        name="Ratio do Passo",
        description="Ratio utilizado em cada passada até a última ajustada",
        default=0.9000,
        min=0.0100,
        max=0.9999,
        precision=4,
        step=1,
    )

    step_count: IntProperty(
        name="Número de Passos",
        description="Quantidade total de passadas de dizimação",
        default=10,
        min=1,
        max=500,
    )

    stack_before_apply: BoolProperty(
        name="Empilhar Modificadores",
        description=(
            "Se marcado, adiciona todas as passadas intermediárias na pilha antes de"
            " aplicar. Se desmarcado, aplica 1 a 1."
        ),
        default=True,
    )


class OBJECT_OT_incremental_decimate_apply(Operator):
    bl_idname = "object.incremental_decimate_apply"
    bl_label = "Aplicar Dizimação Incremental"
    bl_description = (
        "Calcula e aplica modificadores Decimate de forma incremental até atingir o"
        " alvo exato"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and context.active_object.type == "MESH"

    def execute(self, context):
        obj = context.active_object
        props = context.scene.incremental_decimate_props

        # 1. Garante Object Mode e permanece nele
        if obj.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")

        t_0 = get_mesh_triangle_count(obj)
        t_target = props.target_triangles

        # Validação de Alvo
        if t_target >= t_0:
            self.report(
                {"WARNING"},
                f"Target de triângulos ({t_target:,}) é maior ou igual ao número atual ({t_0:,}).",
            )
            return {"CANCELLED"}

        # 2. Cálculo do número de passadas intermediárias e do ratio base
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
            """Configura as propriedades do modificador Decimate em modo Collapse."""
            mod.decimate_type = "COLLAPSE"
            mod.ratio = max(min(ratio_val, 1.0), 0.0001)
            mod.use_collapse_triangulate = props.use_triangulate
            mod.use_symmetry = props.use_symmetry
            mod.symmetry_axis = props.symmetry_axis
            if props.vertex_group and props.vertex_group in obj.vertex_groups:
                mod.vertex_group = props.vertex_group
                mod.invert_vertex_group = props.invert_vertex_group
                mod.vertex_group_factor = props.vertex_group_factor

        # 3. Execução das passadas intermediárias
        if num_intermediate_passes > 0:
            if props.stack_before_apply:
                # Caso A: Empilhar em lote e aplicar todos de uma vez
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
                # Caso B: Aplicar 1 a 1 sequencialmente
                for i in range(num_intermediate_passes):
                    mod = obj.modifiers.new(
                        name=f"IncDecimate_Pass_{i+1}", type="DECIMATE"
                    )
                    setup_modifier_props(mod, r_step)
                    bpy.ops.object.modifier_apply(modifier=mod.name)

        # 4. Medição real e cálculo do ratio para a última passada
        t_atual = get_mesh_triangle_count(obj)

        if t_atual > t_target:
            r_final = t_target / t_atual
            final_mod = obj.modifiers.new(name="IncDecimate_Final", type="DECIMATE")
            setup_modifier_props(final_mod, r_final)
            bpy.ops.object.modifier_apply(modifier=final_mod.name)

        t_final = get_mesh_triangle_count(obj)
        self.report(
            {"INFO"},
            f"Dizimação concluída: {t_0:,} → {t_final:,} triângulos (Target: {t_target:,}).",
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

        # Painel Informativo da Malha
        box_info = layout.box()
        if obj and obj.type == "MESH":
            t_current = get_mesh_triangle_count(obj)
            box_info.label(
                text=f"Triângulos Atuais: {t_current:,}", icon="MESH_DATA"
            )
        else:
            box_info.label(
                text="Selecione um objeto do tipo Malha (Mesh)", icon="INFO"
            )
            return

        col = layout.column(align=True)
        col.use_property_split = True
        col.use_property_decorate = False

        col.prop(props, "target_triangles", text="Target Triangles")
        col.prop(props, "use_triangulate", text="Triangulate")

        # Simetria e Eixos
        row_sym = col.row(align=True)
        row_sym.prop(props, "use_symmetry", text="Symmetry")
        sub = row_sym.row(align=True)
        sub.enabled = props.use_symmetry
        sub.prop(props, "symmetry_axis", expand=True)

        # Vertex Group
        col.prop_search(
            props, "vertex_group", obj, "vertex_groups", text="Vertex Group"
        )
        if props.vertex_group:
            row_vg = col.row(align=True)
            row_vg.prop(
                props, "invert_vertex_group", text="", icon="ARROW_LEFTRIGHT"
            )
            row_vg.prop(props, "vertex_group_factor", text="Factor")

        layout.separator()

        # Configurações do Incremento
        box_inc = layout.box()
        box_inc.label(text="Increment Settings", icon="MOD_DECIM")

        col_inc = box_inc.column(align=True)
        col_inc.use_property_split = True
        col_inc.use_property_decorate = False

        col_inc.prop(props, "mode", text="Mode")

        if props.mode == "FIXED_RATIO":
            col_inc.prop(props, "step_ratio", text="Step Ratio")
        else:
            col_inc.prop(props, "step_count", text="Step Count")

        col_inc.prop(props, "stack_before_apply", text="Empilhar em Lote")

        layout.separator()

        # Botão de Execução
        btn_col = layout.column()
        btn_col.scale_y = 1.4
        btn_col.operator(
            "object.incremental_decimate_apply",
            text="Aplicar Dizimação",
            icon="PLAY",
        )


classes = (
    IncrementalDecimateProperties,
    OBJECT_OT_incremental_decimate_apply,
    OBJECT_PT_incremental_decimate_panel,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.incremental_decimate_props = PointerProperty(
        type=IncrementalDecimateProperties
    )


def unregister():
    del bpy.types.Scene.incremental_decimate_props
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()