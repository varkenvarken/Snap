# ##### BEGIN GPL LICENSE BLOCK #####
#
#  Snap!, position modular assets with ease.
#  (c) 2021 - 2025 Michel Anders (varkenvarken)
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU General Public License
#  as published by the Free Software Foundation; either version 2
#  of the License, or (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program; if not, write to the Free Software Foundation,
#  Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA.
#
# ##### END GPL LICENSE BLOCK #####

bl_info = {
    "name": "Snap!",
    "author": "Michel Anders (varkenvarken)",
    "version": (0, 0, 20250428135306),
    "blender": (4, 2, 1),
    "location": "View3D > Options panel",
    "description": "Position and snap modular assets",
    "warning": "",
    "wiki_url": "",
    "tracker_url": "",
    "category": "Object",
}
from fnmatch import fnmatchcase
from functools import partial
from math import cos, degrees, isclose, pi, radians, sin

import blf
import bpy
import gpu
import numpy as np
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    FloatVectorProperty,
    IntProperty,
    IntVectorProperty,
    PointerProperty,
    StringProperty,
)
from bpy.types import Menu, PropertyGroup
from bpy_extras import view3d_utils
from gpu_extras.batch import batch_for_shader
from gpu_extras.presets import draw_circle_2d
from mathutils import Matrix, Quaternion, Vector, geometry, kdtree

from .utils import load_icons

POINTS = ("A", "B", "C", "D")
DISK_SEGMENTS = 32

uniform_shader = gpu.shader.from_builtin("UNIFORM_COLOR")
smooth_shader_2d = gpu.shader.from_builtin("SMOOTH_COLOR")


def draw_line(p0, p1, color):
    batch = batch_for_shader(uniform_shader, "LINES", {"pos": [p0, p1]})
    uniform_shader.bind()
    uniform_shader.uniform_float("color", color)
    batch.draw(uniform_shader)


unit_circle = np.array(
    [(0, 0)]
    + [
        (cos(2 * pi * t / DISK_SEGMENTS), sin(2 * pi * t / DISK_SEGMENTS))
        for t in range(DISK_SEGMENTS + 1)
    ],
    dtype=np.float32,
)
indices = [(0, i, i + 1) for i in range(1, DISK_SEGMENTS + 1)]
colors = np.zeros((DISK_SEGMENTS + 2, 4), dtype=np.float32)

# a list of vertices in triangle fan order
N = 16
cone = np.array(
    [(0.0, 0.0, 3.0)]
    + [(sin(2 * pi * i / N), cos(2 * pi * i / N), 0.0) for i in range(N + 1)],
    dtype=np.float32,
)
cone_indices = [(0, i + 1, i + 2) for i in range(N)]

tcone = np.zeros((N * 3, 3), dtype=np.float32)
for i in range(N):
    j = i * 3
    tcone[j] = (0, 0, 3)
    tcone[j + 1] = (sin(2 * pi * i / N), cos(2 * pi * i / N), 0.0)
    tcone[j + 2] = (
        sin(2 * pi * ((i + 1) % N) / N),
        cos(2 * pi * ((i + 1) % N) / N),
        0.0,
    )


def draw_disk(pos, radius, color):
    # print("draw_disk", pos, radius, color)
    circle = (unit_circle * radius + np.array((pos.x, pos.y), dtype=np.float32))[
        indices
    ]
    circle.shape = -1, 2
    colors[:, :3] = color
    colors[0, 3] = 1
    colors[1:, 3] = 0
    circle_colors = colors[indices].reshape(-1, 4)
    batch = batch_for_shader(
        smooth_shader_2d, "TRIS", {"pos": circle, "color": circle_colors}
    )
    uniform_shader.bind()
    batch.draw(smooth_shader_2d)


def draw_cone(pos, direction, color, scale):
    global buf
    global view_matrix

    Z = Vector((0, 0, 1))
    csize = 0.06 * scale
    rot = np.array(direction.rotation_difference(Z).to_matrix(), dtype=np.float32)
    rcone = np.empty_like(tcone)
    # TODO do this all in one go
    for i in range(len(tcone)):
        rcone[i] = tcone[i] @ rot
    rcone = csize * rcone
    rcone += pos
    batch = batch_for_shader(
        uniform_shader,
        "TRIS",
        {"pos": rcone},
        indices=np.arange(len(rcone) * 3, dtype=np.int32).reshape(-1, 3),
    )
    uniform_shader.bind()
    uniform_shader.uniform_float("color", color)
    batch.draw(uniform_shader)


def draw_handler_post_view():
    # draw coordinate axes of snappoints on selected objects
    prefs = bpy.context.preferences.addons[__name__].preferences
    if prefs.visible:
        color_direction = prefs.dircolor
        color_up = prefs.upcolor
        color_right = prefs.rightcolor
        for ob in bpy.context.selected_objects:
            if ob.snapper.snapper:
                if prefs.debug and prefs.dump:
                    print("=" * 20)
                    print(f"{ob.matrix_world = }")
                for point in POINTS:
                    if not getattr(ob.snapper, f"{point}_disable"):
                        loc = Vector(getattr(ob.snapper, f"{point}_location"))
                        scale = getattr(ob.snapper, f"{point}_gizmoscale")
                        p0 = ob.matrix_world @ loc
                        p1 = ob.matrix_world @ (
                            loc
                            + scale * Vector(getattr(ob.snapper, f"{point}_direction"))
                        )
                        p2 = ob.matrix_world @ (
                            loc + scale * Vector(getattr(ob.snapper, f"{point}_up"))
                        )
                        p3 = ob.matrix_world @ (
                            loc + scale * Vector(getattr(ob.snapper, f"{point}_right"))
                        )
                        # debug info if requested
                        if prefs.debug and prefs.dump:
                            print(f"{point = }")
                            print(f"{loc = }")
                            print(f"{scale = }")
                            print(f"{p0 = }")
                            print(f"{p1 = }")
                            print(f"{p2 = }")
                            print(f"{p3 = }")
                        if not (prefs.debug and prefs.noarrows):
                            gpu.state.line_width_set(prefs.linewidth)
                            draw_line(p0, p1, color_direction)
                            draw_line(p0, p2, color_up)
                            draw_line(p0, p3, color_right)
                            gpu.state.line_width_set(1)
                        if not (prefs.debug and prefs.nocones):
                            cscale = scale * prefs.conescale
                            draw_cone(p1, p1 - p0, color_direction, cscale)
                            draw_cone(p2, p2 - p0, color_up, cscale)
                            draw_cone(p3, p3 - p0, color_right, cscale)
                for point in ob.snappoints:
                    if not point.disable:
                        loc = Vector(point.location)
                        scale = point.gizmoscale
                        p0 = ob.matrix_world @ loc
                        p1 = ob.matrix_world @ (loc + scale * Vector(point.direction))
                        p2 = ob.matrix_world @ (loc + scale * Vector(point.up))
                        p3 = ob.matrix_world @ (loc + scale * Vector(point.right))

                        # debug info if requested
                        if prefs.debug and prefs.dump:
                            print(f"{point = }")
                            print(f"{loc = }")
                            print(f"{scale = }")
                            print(f"{p0 = }")
                            print(f"{p1 = }")
                            print(f"{p2 = }")
                            print(f"{p3 = }")
                        if not (prefs.debug and prefs.noarrows):
                            gpu.state.line_width_set(prefs.linewidth)
                            draw_line(p0, p1, color_direction)
                            draw_line(p0, p2, color_up)
                            draw_line(p0, p3, color_right)
                            gpu.state.line_width_set(1)
                        if not (prefs.debug and prefs.nocones):
                            cscale = scale * prefs.conescale
                            draw_cone(p1, p1 - p0, color_direction, cscale)
                            draw_cone(p2, p2 - p0, color_up, cscale)
                            draw_cone(p3, p3 - p0, color_right, cscale)
                if prefs.debug and prefs.dump:
                    print("=" * 20)


def draw_handler_post_pixel():
    prefs = bpy.context.preferences.addons[__name__].preferences
    if prefs.visible:
        # bgl.glEnable(bgl.GL_BLEND)
        # bgl.glBlendEquation(bgl.GL_FUNC_ADD)
        # bgl.glBlendFunc(bgl.GL_SRC_ALPHA, bgl.GL_ONE_MINUS_SRC_ALPHA)
        # highlight the pair of closest snappoints (for interactive snapping)
        gpu.state.blend_set("ALPHA")
        global from_point
        global to_point
        if from_point:
            coords_2d = view3d_utils.location_3d_to_region_2d(
                region=bpy.context.region,
                rv3d=bpy.context.space_data.region_3d,
                coord=from_point,
            )
            if coords_2d:
                draw_disk(coords_2d, 20, prefs.fromcolor)
        if to_point:
            coords_2d = view3d_utils.location_3d_to_region_2d(
                region=bpy.context.region,
                rv3d=bpy.context.space_data.region_3d,
                coord=to_point,
            )
            if coords_2d:
                draw_disk(coords_2d, 20, prefs.tocolor)
        # draw labels of snappoints on selected objects
        font_id = 0  # NICE TO HAVE: font based on settings
        if prefs.fontshadow:
            blf.enable(font_id, blf.SHADOW)
            blf.shadow(font_id, 5, 0, 0, 0, 0.7)
            blf.shadow_offset(font_id, 2, -2)
        fontsize = prefs.fontsize
        offset = Vector(prefs.labeloffset)
        for ob in bpy.context.selected_objects:
            if ob.snapper.snapper:
                for point in POINTS:
                    if not getattr(ob.snapper, f"{point}_disable"):
                        p0 = ob.matrix_world @ Vector(
                            getattr(ob.snapper, f"{point}_location")
                        )
                        coords_2d = view3d_utils.location_3d_to_region_2d(
                            region=bpy.context.region,
                            rv3d=bpy.context.space_data.region_3d,
                            coord=p0,
                        )
                        if coords_2d:
                            coords_2d += offset
                            blf.position(0, *coords_2d, 0)
                            blf.size(font_id, fontsize)
                            if prefs.coloroverride:
                                blf.color(font_id, *(prefs.replacementcolor))
                            else:
                                blf.color(
                                    font_id, *getattr(ob.snapper, f"{point}_labelcolor")
                                )
                            blf.draw(font_id, getattr(ob.snapper, f"{point}_label"))
                for point in ob.snappoints:
                    if not point.disable:
                        p0 = ob.matrix_world @ Vector(point.location)
                        coords_2d = view3d_utils.location_3d_to_region_2d(
                            region=bpy.context.region,
                            rv3d=bpy.context.space_data.region_3d,
                            coord=p0,
                        )
                        if coords_2d:
                            coords_2d += offset
                            blf.position(0, *coords_2d, 0)
                            blf.size(font_id, fontsize)
                            if prefs.coloroverride:
                                blf.color(font_id, *(prefs.replacementcolor))
                            else:
                                blf.color(font_id, *point.labelcolor)
                            blf.draw(font_id, point.label)


def ensure_ortho_right(self, context, point="A"):
    setattr(
        self,
        f"{point}_right",
        Vector(getattr(self, f"{point}_direction"))
        .cross(Vector(getattr(self, f"{point}_up")))
        .normalized(),
    )


def ensure_ortho_right_extra(self, context):
    setattr(
        self,
        f"right",
        Vector(getattr(self, f"direction"))
        .cross(Vector(getattr(self, f"up")))
        .normalized(),
    )


class SnapperPointPropertyGroup(bpy.types.PropertyGroup):
    label: StringProperty(
        name="label",
        default="Label",
        description="Descriptive label for this snap-point",
    )
    disable: BoolProperty(
        name="Disable", default=False, description="Disable this snap-point"
    )
    location: FloatVectorProperty(name="loc", description="Location")
    direction: FloatVectorProperty(
        name="dir",
        default=(1, 0, 0),
        description="Direction",
        update=ensure_ortho_right_extra,
    )
    up: FloatVectorProperty(
        name="up",
        default=(0, 0, 1),
        description="Up vector",
        update=ensure_ortho_right_extra,
    )
    right: FloatVectorProperty(
        name="right",
        default=Vector((1, 0, 0)).cross(Vector((0, 0, 1))),
        description="Right hand vector (calculated automatically)",
    )
    snapangle: FloatProperty(
        name="angle",
        default=radians(45),
        description="restrict rotation around principle direction to steps of this size",
        subtype="ANGLE",
        unit="ROTATION",
        soft_min=radians(1),
        min=radians(0.01),
        max=pi,
        step=100,
    )
    labelcolor: FloatVectorProperty(
        name="color",
        size=4,
        default=(1, 1, 1, 1),
        description="Label color",
        subtype="COLOR",
    )
    gizmoscale: FloatProperty(
        name="scale",
        default=1,
        description="Size of the snappoint display axes",
        min=0.00001,
        unit="LENGTH",
    )
    tags: StringProperty(
        name="tags",
        default="",
        description="A comma separated list of tags for this snap-point",
    )
    accepttags: StringProperty(
        name="accept tags",
        default="",
        description="A comma separated list of acceptable tags for this snap-point",
    )


class SnapperPropertyGroup(bpy.types.PropertyGroup):
    snapper: BoolProperty(name="Snapper", default=False)


annotations = SnapperPropertyGroup.__annotations__
for n, pt in enumerate(POINTS):
    exec(
        f"def {pt}_ensure_ortho_right(self, context): ensure_ortho_right(self, context, point='{pt}')"
    )
    f = globals()[f"{pt}_ensure_ortho_right"]
    annotations[f"{pt}_disable"] = BoolProperty(
        name="Disable", default=(n > 0), description="Disable this snap-point"
    )
    annotations[f"{pt}_location"] = FloatVectorProperty(
        name="loc", description="Location"
    )
    annotations[f"{pt}_direction"] = FloatVectorProperty(
        name="dir", default=(1, 0, 0), update=f, description="Direction"
    )
    annotations[f"{pt}_up"] = FloatVectorProperty(
        name="up", default=(0, 0, 1), update=f, description="Up vector"
    )
    annotations[f"{pt}_right"] = FloatVectorProperty(
        name="right",
        default=Vector((1, 0, 0)).cross(Vector((0, 0, 1))),
        description="Right hand vector (calculated automatically)",
    )
    annotations[f"{pt}_snapangle"] = FloatProperty(
        name="angle",
        default=radians(45),
        description="restrict rotation around principle direction to steps of this size",
        subtype="ANGLE",
        unit="ROTATION",
        soft_min=radians(1),
        min=radians(0.01),
        max=pi,
        step=100,
    )
    annotations[f"{pt}_label"] = StringProperty(
        name="label", default=pt, description="Descriptive label for this snap-point"
    )
    annotations[f"{pt}_labelcolor"] = FloatVectorProperty(
        name="color",
        size=4,
        default=(1, 1, 1, 1),
        description="Label color",
        subtype="COLOR",
    )
    annotations[f"{pt}_gizmoscale"] = FloatProperty(
        name="scale",
        default=1,
        description="Size of the snappoint display axes",
        min=0.00001,
        unit="LENGTH",
    )
    annotations[f"{pt}_tags"] = StringProperty(
        name="tags",
        default="",
        description="A comma separated list of tags for this snap-point",
    )
    annotations[f"{pt}_accepttags"] = StringProperty(
        name="accept tags",
        default="",
        description="A comma separated list of acceptable tags for this snap-point",
    )


class SNAPPER_PT_Snappoints(bpy.types.Panel):
    bl_label = "Point definitions"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Snap!"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(self, context):
        return context.active_object is not None

    def draw(self, context):
        global icons
        ob = context.active_object
        layout = self.layout
        if not ob.select_get():
            layout.label(text="No object selected")
            return
        op = layout.operator(
            "object.snapper_snapenable",
            icon_value=(
                icons["snap_icon"].icon_id
                if ob.snapper.snapper
                else icons["snap_off_icon"].icon_id
            ),
            text="Disable Snap!" if ob.snapper.snapper else "Enable Snap!",
        )
        if ob.snapper.snapper:
            for point in POINTS:
                box = layout.box()

                enabled = not getattr(ob.snapper, f"{point}_disable")
                row = box.row()
                row.prop(ob.snapper, f"{point}_label", text="")
                row.prop(
                    ob.snapper,
                    f"{point}_disable",
                    text="",
                    icon_value=(
                        icons["snap_off_icon"].icon_id
                        if getattr(ob.snapper, f"{point}_disable")
                        else icons["snap_icon"].icon_id
                    ),
                )
                row.prop(ob.snapper, f"{point}_labelcolor", text="")

                row = box.row()
                row.enabled = enabled
                col1 = row.column()
                col2 = row.column()

                col1.row().prop(ob.snapper, f"{point}_location")
                col1.row().prop(ob.snapper, f"{point}_direction")
                col1.row().prop(ob.snapper, f"{point}_up")
                row = col1.row()
                row.prop(ob.snapper, f"{point}_right")
                row.enabled = False
                row = col1.row()
                row.prop(ob.snapper, f"{point}_snapangle")
                row.prop(ob.snapper, f"{point}_gizmoscale")
                col2.row().operator(
                    "object.snapper_set_location",
                    icon_value=icons["pos_icon"].icon_id,
                    text="",
                ).point = point
                brow = col2.row()
                brow.operator(
                    "object.snapper_set_direction",
                    icon_value=icons["dir_icon"].icon_id,
                    text="",
                ).point = point
                brow.operator(
                    "object.snapper_set_direction_to_normal",
                    icon_value=icons["normal_icon"].icon_id,
                    text="",
                ).point = point
                col2.row().operator(
                    "object.snapper_set_up",
                    icon_value=icons["up_icon"].icon_id,
                    text="",
                ).point = point
                col2.row().operator(
                    "object.snapper_reset",
                    icon_value=icons["reset_icon"].icon_id,
                    text="",
                ).point = point
                col2.row().operator(
                    "object.snapper_cycleaxes",
                    icon_value=icons["cycle_icon"].icon_id,
                    text="",
                ).point = point
                row = box.row()
                row.enabled = enabled
                row.prop(
                    ob.snapper,
                    f"{point}_tags",
                    text="",
                    icon_value=icons["connect_icon"].icon_id,
                )
                row.prop(
                    ob.snapper,
                    f"{point}_accepttags",
                    text="",
                    icon_value=icons["accept_icon"].icon_id,
                )


def all_operators(layout, context):
    ob = context.active_object
    layout.prop(bpy.context.preferences.addons[__name__].preferences, "visible")
    if not (ob and ob.snapper.snapper and ob.select_get()):
        layout.label(text="No object selected")
        return
    row = layout.row()
    op = row.operator("object.snapper_snapmodal", icon_value=icons["snap_icon"].icon_id)
    op = row.operator(
        "object.snapper_snapmodal_dup",
        icon_value=icons["snap_icon"].icon_id,
        text="Dup & Snap",
    )
    op.duplicate = True
    op.link = False
    op = row.operator(
        "object.snapper_snapmodal_dup",
        icon_value=icons["snap_icon"].icon_id,
        text="Link & Snap",
    )
    op.duplicate = True
    op.link = True
    op = row.operator("object.snapper_copy", icon_value=icons["copy_icon"].icon_id)
    if all(getattr(ob.snapper, f"{pt}_disable") for pt in POINTS):
        row.enabled = False
        row.label(text="no snap-points enabled")
    op = row.operator("object.snapper_select", icon="SELECT_SET")
    op.all = False

    # all flip operators
    box = layout.box()
    row = box.row()
    split = row.split(factor=0.4)
    col = split.column()
    col.operator(
        "object.snapper_flip",
        icon_value=icons["flip_icon"].icon_id,
        text="Flip: " + getattr(ob.snapper, "A_label"),
    ).point = "A"
    col.enabled = not ob.snapper.A_disable
    split = split.split()
    row = split.row()
    col = row.column()
    col.operator("object.snapper_flip", text=getattr(ob.snapper, "B_label")).point = "B"
    col.enabled = not ob.snapper.B_disable
    col = row.column()
    col.operator("object.snapper_flip", text=getattr(ob.snapper, "C_label")).point = "C"
    col.enabled = not ob.snapper.C_disable
    col = row.column()
    col.operator("object.snapper_flip", text=getattr(ob.snapper, "D_label")).point = "D"
    col.enabled = not ob.snapper.D_disable

    if len(ob.snappoints):
        row = box.row()
        for i, pt in enumerate(ob.snappoints):
            if i % 10 == 0:
                row = box.row()
            col = row.column()
            col.operator("object.snapper_flip_extra", text=pt.label).point = i
            col.enabled = not pt.disable

    # all rotate operators
    box = layout.box()
    row = box.row()
    split = row.split(factor=0.4)
    col = split.column()
    col.operator(
        "object.snapper_rotate",
        icon_value=icons["rotate_icon"].icon_id,
        text="Rotate: " + getattr(ob.snapper, "A_label"),
    ).point = "A"
    col.enabled = not ob.snapper.A_disable
    split = split.split()
    row = split.row()
    col = row.column()
    col.operator("object.snapper_rotate", text=getattr(ob.snapper, "B_label")).point = (
        "B"
    )
    col.enabled = not ob.snapper.B_disable
    col = row.column()
    col.operator("object.snapper_rotate", text=getattr(ob.snapper, "C_label")).point = (
        "C"
    )
    col.enabled = not ob.snapper.C_disable
    col = row.column()
    col.operator("object.snapper_rotate", text=getattr(ob.snapper, "D_label")).point = (
        "D"
    )
    col.enabled = not ob.snapper.D_disable

    if len(ob.snappoints):
        row = box.row()
        for i, pt in enumerate(ob.snappoints):
            if i % 10 == 0:
                row = box.row()
            col = row.column()
            col.operator("object.snapper_rotate_extra", text=pt.label).point = i
            col.enabled = not pt.disable

    # all cursor snap operators
    box = layout.box()
    row = box.row()
    split = row.split(factor=0.4)
    col = split.column()
    col.operator(
        "object.snapper_cursor",
        text="Snap cursor to: " + getattr(ob.snapper, "A_label"),
        icon="CURSOR",
    ).point = "A"
    col.enabled = not ob.snapper.A_disable
    split = split.split()
    row = split.row()
    col = row.column()
    col.operator("object.snapper_cursor", text=getattr(ob.snapper, "B_label")).point = (
        "B"
    )
    col.enabled = not ob.snapper.B_disable
    col = row.column()
    col.operator("object.snapper_cursor", text=getattr(ob.snapper, "C_label")).point = (
        "C"
    )
    col.enabled = not ob.snapper.C_disable
    col = row.column()
    col.operator("object.snapper_cursor", text=getattr(ob.snapper, "D_label")).point = (
        "D"
    )
    col.enabled = not ob.snapper.D_disable

    if len(ob.snappoints):
        row = box.row()
        for i, pt in enumerate(ob.snappoints):
            if i % 10 == 0:
                row = box.row()
            col = row.column()
            col.operator("object.snapper_cursor_extra", text=pt.label).point = i
            col.enabled = not pt.disable


class SNAPPER_PT_Operators(bpy.types.Panel):
    bl_label = "Snap!"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Snap!"
    bl_options = set()

    @classmethod
    def poll(self, context):
        return True

    def draw(self, context):
        layout = self.layout
        all_operators(layout, context)
        row = layout.row()
        row.operator(
            "WM_OT_call_menu_pie",
            text="Snap! Pie menu",
            icon_value=icons["pie_icon"].icon_id,
        ).name = "SNAPPER_MT_Pie"
        row.prop(context.preferences.addons[__name__].preferences, "flip")
        row.prop(context.preferences.addons[__name__].preferences, "autoparent")
        row.prop(context.preferences.addons[__name__].preferences, "moveselected")
        row.prop(context.preferences.addons[__name__].preferences, "matchtags")


class SNAPPER_PT_PointCollection(bpy.types.Panel):
    bl_label = "Extra point definitions"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Snap!"
    bl_options = set()

    @classmethod
    def poll(self, context):
        return True

    def draw(self, context):
        layout = self.layout
        if not context.active_object.select_get():
            layout.label(text="No object selected")
            return
        # col = layout.column()
        layout.template_list(
            "POINTS_UL_Snapper",
            "",
            context.object,
            "snappoints",
            context.object,
            "active_snappoint",
            rows=3,
            maxrows=3,
        )
        layout.operator("object.snapper_point_add", icon="ADD", text="")


class SNAPPER_OT_PointAdd(bpy.types.Operator):
    bl_idname = "object.snapper_point_add"
    bl_label = "Add"
    bl_description = "Add a snappoint"
    bl_options = {"REGISTER", "UNDO", "PRESET"}

    @classmethod
    def poll(self, context):
        return True

    def execute(self, context):
        pt = context.object.snappoints.add()
        pt.label = str(len(context.object.snappoints))

        scale = max(0.2, min(context.active_object.dimensions))
        pt.gizmoscale = scale

        # all kinds of hacks to cause the 3d view to redraw and show the new state
        context.scene.render.preview_pixel_size = (
            context.scene.render.preview_pixel_size
        )
        context.active_object.update_tag()
        context.scene.update_tag()
        context.view_layer.update()
        return {"FINISHED"}


class SNAPPER_OT_PointRemove(bpy.types.Operator):
    bl_idname = "object.snapper_point_remove"
    bl_label = "Remove"
    bl_description = "Remove this snappoint"
    bl_options = {"REGISTER", "UNDO", "PRESET"}

    index: IntProperty(name="Index", default=-1)

    @classmethod
    def poll(self, context):
        return (
            len(context.object.snappoints) > 0
        )  # we will not try to remove from an empty list

    def execute(self, context):
        # originalindex = context.object.active_snappoint
        # context.object.snappoints.remove(context.object.active_snappoint)
        context.object.snappoints.remove(self.index)
        context.object.active_snappoint = self.index - 1 if self.index else 0

        # all kinds of hacks to cause the 3d view to redraw and show the new state
        context.scene.render.preview_pixel_size = (
            context.scene.render.preview_pixel_size
        )
        context.active_object.update_tag()
        context.scene.update_tag()
        context.view_layer.update()

        return {"FINISHED"}


class POINTS_UL_Snapper(
    bpy.types.UIList
):  # we cannot derive from UI_UL_list, a bug apparently
    def draw_item(
        self, context, layout, data, item, icon, active_data, active_propname, index
    ):
        colormap = item
        if self.layout_type in {
            "DEFAULT",
            "COMPACT",
            "GRID",
        }:  # we basically ignore the distinction
            # layout.label(text="extra snappoint")
            box = layout.box()
            col = box.column()

            row = col.row()
            row.prop(item, "label", text="")

            row.prop(
                item,
                "disable",
                text="",
                icon_value=(
                    icons["snap_off_icon"].icon_id
                    if item.disable
                    else icons["snap_icon"].icon_id
                ),
            )
            row.prop(item, "labelcolor", text="")
            row = col.row()
            row.enabled = not item.disable
            col1 = row.column()
            col2 = row.column()

            col1.row().prop(item, "location")
            col2.row().operator(
                "object.snapper_set_location_extra",
                icon_value=icons["pos_icon"].icon_id,
                text="",
            ).point = index

            col1.row().prop(item, "direction")
            brow = col2.row()
            brow.operator(
                "object.snapper_set_direction_extra",
                icon_value=icons["dir_icon"].icon_id,
                text="",
            ).point = index
            brow.operator(
                "object.snapper_set_direction_to_normal_extra",
                icon_value=icons["normal_icon"].icon_id,
                text="",
            ).point = index
            col1.row().prop(item, "up")
            col2.row().operator(
                "object.snapper_set_up_extra",
                icon_value=icons["up_icon"].icon_id,
                text="",
            ).point = index
            rrow = col1.row()
            rrow.enabled = False
            rrow.prop(item, "right")
            col2.row().operator(
                "object.snapper_reset_extra",
                icon_value=icons["reset_icon"].icon_id,
                text="",
            ).point = index
            row = col1.row()
            row.prop(item, "snapangle")
            row.prop(item, "gizmoscale")
            col2.row().operator(
                "object.snapper_cycleaxes_extra",
                icon_value=icons["cycle_icon"].icon_id,
                text="",
            ).point = index
            row = col.row()
            row.prop(item, "tags", text="", icon_value=icons["connect_icon"].icon_id)
            row.prop(
                item, "accepttags", text="", icon_value=icons["accept_icon"].icon_id
            )
            row = col.row()
            row.operator("object.snapper_point_add", icon="ADD", text="")
            row.operator(
                "object.snapper_point_remove", icon="REMOVE", text=""
            ).index = index

    # Called once to filter/reorder items.
    def filter_items(self, context, data, propname):
        points = getattr(data, propname)

        flt_flags = []
        if len(self.filter_name.strip()) and len(points):
            flt_flags = [
                (
                    self.bitflag_filter_item
                    if fnmatchcase(pt.label, self.filter_name)
                    else 0
                )
                for pt in points
            ]

        flt_neworder = []
        if self.use_filter_sort_alpha and len(points):
            st = [(pt.label, n) for n, pt in enumerate(points)]
            st.sort()  # reversing is done by the object (it looks at the reverse flag)
            flt_neworder = [0] * len(points)
            for new_index, s in enumerate(st):
                flt_neworder[s[1]] = new_index

        return flt_flags, flt_neworder


def dir_is_aligned(ob, ob2, snappoint="A", snappoint2="A"):
    print(ob, ob2, snappoint, snappoint2)
    to_direction = (
        getattr(ob.snapper, snappoint + "_direction")
        if type(snappoint) == str
        else ob.snappoints[snappoint].direction
    )
    from_direction = (
        getattr(ob2.snapper, snappoint2 + "_direction")
        if type(snappoint2) == str
        else ob2.snappoints[snappoint2].direction
    )

    to_direction_ls = Vector(to_direction).to_4d()
    to_direction_ls.w = 0
    to_direction_ws = ob.matrix_world @ to_direction_ls
    from_direction_ls = Vector(from_direction).to_4d()
    from_direction_ls.w = 0
    from_direction_ws = ob2.matrix_world @ from_direction_ls

    return to_direction_ws.dot(from_direction_ws) > 0.0


def align_objects(ob, ob2, snappoint="A", snappoint2="A", rotsteps=0, flip=False):
    """
    Align ob2 to ob1.

    Align snappoint2 in ob2 to snappoint in ob by changing the
    location and rotation of ob2.

    Additionally, rotate ob2 by rotsteps around its principal direction.

    If flip is True, principal directions will be aligned to be anti-parallel.

    Returns the original angles between the principal directions and the up-vectors.
    """
    print(f"align {ob2}:{snappoint2} to {ob}:{snappoint} flip={flip}")
    epsilon = 0.0001

    to_location = (
        getattr(ob.snapper, snappoint + "_location")
        if type(snappoint) == str
        else ob.snappoints[snappoint].location
    )
    from_location = (
        getattr(ob2.snapper, snappoint2 + "_location")
        if type(snappoint2) == str
        else ob2.snappoints[snappoint2].location
    )
    to_direction = (
        getattr(ob.snapper, snappoint + "_direction")
        if type(snappoint) == str
        else ob.snappoints[snappoint].direction
    )
    from_direction = (
        getattr(ob2.snapper, snappoint2 + "_direction")
        if type(snappoint2) == str
        else ob2.snappoints[snappoint2].direction
    )
    to_up = (
        getattr(ob.snapper, snappoint + "_up")
        if type(snappoint) == str
        else ob.snappoints[snappoint].up
    )
    from_up = (
        getattr(ob2.snapper, snappoint2 + "_up")
        if type(snappoint2) == str
        else ob2.snappoints[snappoint2].up
    )
    to_snapangle = (
        getattr(ob.snapper, snappoint + "_snapangle")
        if type(snappoint) == str
        else ob.snappoints[snappoint].snapangle
    )

    # calculate translation
    to_location_ws = ob.matrix_world @ Vector(to_location)
    from_location_ws = ob2.matrix_world @ Vector(from_location)
    translation = to_location_ws - from_location_ws

    ob2.matrix_world = Matrix.Translation(translation) @ ob2.matrix_world

    # calculate rotation needed to align principal directions
    to_direction_ls = Vector(to_direction).to_4d()
    to_direction_ls.w = 0
    to_direction_ws = ob.matrix_world @ to_direction_ls
    from_direction_ls = Vector(from_direction).to_4d()
    from_direction_ls.w = 0
    if flip:
        from_direction_ls = -from_direction_ls
    from_direction_ws = ob2.matrix_world @ from_direction_ls

    rot = from_direction_ws.rotation_difference(to_direction_ws)
    principle_angle = abs(rot.angle)
    rotm = rot.to_matrix().to_4x4()
    if principle_angle < epsilon:
        rotm = Matrix()  # == I(4)
    elif abs(principle_angle - pi) < epsilon:
        # rotm = Matrix.Diagonal((-1,-1,-1,1))  # inversion matrix (not to be confused with a inverted matrix)
        axis = from_direction_ws.to_3d().orthogonal().normalized()
        rotm = Matrix.Rotation(
            pi, 4, axis
        )  # 180 degree rotation around arbitrary axis perp. to directions
    M = Matrix.Translation(to_location_ws) @ rotm @ Matrix.Translation(-to_location_ws)
    ob2.matrix_world = M @ ob2.matrix_world

    # calculate rotation to align the up vectors
    to_up_ls = Vector(to_up).to_4d()
    to_up_ls.w = 0
    from_up_ls = Vector(from_up).to_4d()
    from_up_ls.w = 0
    to_up_ws = ob.matrix_world @ to_up_ls
    from_up_ws = ob2.matrix_world @ from_up_ls
    rot = from_up_ws.rotation_difference(to_up_ws)

    up_angle = abs(rot.angle)

    if up_angle < epsilon:
        rot = Quaternion()
    elif abs(up_angle - pi) < epsilon:
        rot = Quaternion(
            to_direction_ws.xyz, pi
        )  # rotation of 180d around direction vector

    rot2 = Quaternion()
    if rotsteps != 0:
        rot2 = Quaternion(to_direction_ws, Vector(to_snapangle) * rotsteps)

    M = (
        Matrix.Translation(to_location_ws)
        @ rot2.to_matrix().to_4x4()
        @ rot.to_matrix().to_4x4()
        @ Matrix.Translation(-to_location_ws)
    )
    ob2.matrix_world = M @ ob2.matrix_world

    return principle_angle, up_angle


# TODO check if unused
def closest_pair(ob, ob2):
    epsilon = 0.0001

    # find out which snap-points are closest (ambiguous if multiple points overlap)
    distance = 1e30
    pair = None
    for snappoint in POINTS:
        for snappoint2 in POINTS:
            to_location = snappoint + "_location"
            from_location = snappoint2 + "_location"
            to_location_ws = ob.matrix_world @ Vector(getattr(ob.snapper, to_location))
            from_location_ws = ob2.matrix_world @ Vector(
                getattr(ob2.snapper, from_location)
            )
            translation = to_location_ws - from_location_ws
            d = translation.length
            if d < distance:
                distance = d
                pair = (snappoint, snappoint2, to_location_ws, from_location_ws)
    return pair


def flip_pair(ob, other_obs, snappoint):
    epsilon = 0.0001

    if type(snappoint) == str:
        to_location = Vector(getattr(ob.snapper, snappoint + "_location"))
    else:
        to_location = Vector(ob.snappoints[snappoint].location)
    to_location_ws = ob.matrix_world @ to_location

    # find out which snap-point on another object is closest to the given snap-point
    distance = 1e30
    pair = None
    for ob2 in other_obs:
        for snappoint2 in POINTS:
            from_location = snappoint2 + "_location"
            from_location_ws = ob2.matrix_world @ Vector(
                getattr(ob2.snapper, from_location)
            )
            translation = to_location_ws - from_location_ws
            d = translation.length
            if d < distance:
                distance = d
                pair = (ob2, snappoint, snappoint2, to_location_ws, from_location_ws)
        for n, snappoint2 in enumerate(ob2.snappoints):
            from_location = snappoint2.location
            from_location_ws = ob2.matrix_world @ Vector(from_location)
            translation = to_location_ws - from_location_ws
            d = translation.length
            if d < distance:
                distance = d
                pair = (ob2, snappoint, n, to_location_ws, from_location_ws)
    return pair


def rotate_object(ob, snappoint):
    """
    Rotate ob around the principle axis of the snappoint.
    """

    if type(snappoint) == str:
        from_location = Vector(getattr(ob.snapper, snappoint + "_location"))
        from_direction = Vector(getattr(ob.snapper, snappoint + "_direction"))
        from_snapangle = getattr(ob.snapper, snappoint + "_snapangle")
    else:
        from_location = Vector(ob.snappoints[snappoint].location)
        from_direction = Vector(ob.snappoints[snappoint].direction)
        from_snapangle = ob.snappoints[snappoint].snapangle

    from_location_ws = ob.matrix_world @ from_location

    from_direction_ls = from_direction.to_4d()
    from_direction_ls.w = 0
    from_direction_ws = ob.matrix_world @ from_direction_ls

    rot = Quaternion(from_direction_ws.xyz, from_snapangle)

    M = (
        Matrix.Translation(from_location_ws)
        @ rot.to_matrix().to_4x4()
        @ Matrix.Translation(-from_location_ws)
    )
    ob.matrix_world = M @ ob.matrix_world


class SNAPPER_OT_Snap(bpy.types.Operator):
    bl_idname = "object.snapper_snap"
    bl_label = "Snap"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = "Snap to objects using snap points"

    to_point: EnumProperty(
        items=[(p, p, p) for p in POINTS], name="To (active)", default="B"
    )
    from_point: EnumProperty(
        items=[(p, p, p) for p in POINTS], name="From (selected)", default="A"
    )
    rotsteps: IntProperty(name="Rot", default=0)

    @classmethod
    def poll(self, context):
        return (
            context.mode == "OBJECT"
            and context.object
            and context.object.snapper.snapper
        )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "from_point")
        layout.prop(self, "to_point")
        layout.prop(self, "rotsteps")

    # we snap select to active
    # we snap A to A for now
    def execute(self, context):
        ob = context.active_object
        for ob2 in context.selected_objects:
            if ob2 != ob:
                break

        fit = align_objects(
            ob,
            ob2,
            snappoint=self.to_point,
            snappoint2=self.from_point,
            rotsteps=self.rotsteps,
        )
        # context.view_layer.update()  Not needed because happens at end of operator anyway

        return {"FINISHED"}


class SNAPPER_OT_Rotate(bpy.types.Operator):
    bl_idname = "object.snapper_rotate"
    bl_label = "Rotate"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = "Rotate around direction axis in fixed steps"

    point: EnumProperty(items=[(p, p, p) for p in POINTS], name="Point", default="A")

    @classmethod
    def poll(self, context):
        return (
            context.mode == "OBJECT"
            and context.object
            and context.object.snapper.snapper
        )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "point")

    def execute(self, context):
        ob = context.active_object
        rotate_object(ob, self.point)

        return {"FINISHED"}


class SNAPPER_OT_RotateExtra(bpy.types.Operator):
    bl_idname = "object.snapper_rotate_extra"
    bl_label = "Rotate"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = "Rotate around direction axis in fixed steps"

    point: IntProperty(name="Point", default=0)

    @classmethod
    def poll(self, context):
        return (
            context.mode == "OBJECT"
            and context.object
            and context.object.snapper.snapper
        )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "point")

    def execute(self, context):
        ob = context.active_object
        rotate_object(ob, self.point)

        return {"FINISHED"}


class SNAPPER_OT_Flip(bpy.types.Operator):
    bl_idname = "object.snapper_flip"
    bl_label = "Flip"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = "Flip object to alternative position"

    point: EnumProperty(items=[(p, p, p) for p in POINTS], name="Point", default="A")

    @classmethod
    def poll(self, context):
        return (
            context.mode == "OBJECT"
            and context.object
            and context.object.snapper.snapper
        )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "point")

    def execute(self, context):
        ob = context.active_object
        other_obs = [
            ob2 for ob2 in context.visible_objects if ob2.snapper.snapper and ob2 != ob
        ]
        if other_obs:
            (ob2, snappoint, snappoint2, to_location_ws, from_location_ws) = flip_pair(
                ob, other_obs, self.point
            )
            is_aligned = dir_is_aligned(
                ob2, ob, snappoint=snappoint2, snappoint2=snappoint
            )
            align_objects(
                ob2,
                ob,
                snappoint=snappoint2,
                snappoint2=snappoint,
                rotsteps=0,
                flip=is_aligned,
            )
        return {"FINISHED"}


class SNAPPER_OT_FlipExtra(bpy.types.Operator):
    bl_idname = "object.snapper_flip_extra"
    bl_label = "Flip"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = "Flip object to alternative position"

    point: IntProperty(name="Point", default=0)

    @classmethod
    def poll(self, context):
        return (
            context.mode == "OBJECT"
            and context.object
            and context.object.snapper.snapper
        )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "point")

    def execute(self, context):
        ob = context.active_object
        other_obs = [
            ob2 for ob2 in context.visible_objects if ob2.snapper.snapper and ob2 != ob
        ]
        if other_obs:
            (ob2, snappoint, snappoint2, to_location_ws, from_location_ws) = flip_pair(
                ob, other_obs, self.point
            )
            is_aligned = dir_is_aligned(
                ob2, ob, snappoint=snappoint2, snappoint2=snappoint
            )
            align_objects(
                ob2,
                ob,
                snappoint=snappoint2,
                snappoint2=snappoint,
                rotsteps=0,
                flip=is_aligned,
            )
        return {"FINISHED"}


class SetterMixin:
    point: EnumProperty(
        items=[(p, p, p) for p in POINTS], name="To (active)", default="B"
    )

    @classmethod
    def poll(
        self, context
    ):  # we should probably limit this to just one selective/active object, and only a mesh object
        return context.mode in ("EDIT_MESH", "EDIT_CURVE", "EDIT_LATTICE")

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "point")

    def set_attr_to_selected(self, context, attr, relative=False):
        ob = context.active_object
        bpy.ops.object.mode_set(mode="OBJECT")
        if ob.type == "CURVE":
            location = Vector()
            n = 0
            for spline in ob.data.splines:
                if spline.type == "BEZIER":
                    for point in spline.bezier_points:
                        if point.select_left_handle:
                            location += point.handle_left
                            n += 1
                        if point.select_right_handle:
                            location += point.handle_right
                            n += 1
                        if point.select_control_point:
                            location += point.co
                            n += 1
            if n > 0:
                selection = location / n
                if relative:
                    selection -= Vector(getattr(ob.snapper, self.point + "_location"))
                    selection = selection.normalized()
                setattr(ob.snapper, self.point + attr, selection)
        elif ob.type == "LATTICE":
            npoints = len(ob.data.points)
            select = np.empty(npoints, dtype=bool)
            ob.data.points.foreach_get("select", select)
            co = np.empty(npoints * 3, dtype=np.float32)
            ob.data.points.foreach_get("co", co)
            co.shape = -1, 3
            selection = Vector(np.average(co[select], axis=0))
            if relative:
                selection -= Vector(getattr(ob.snapper, self.point + "_location"))
                selection = selection.normalized()
            setattr(ob.snapper, self.point + attr, selection)
        else:
            nverts = len(ob.data.vertices)
            select = np.empty(nverts, dtype=bool)
            ob.data.vertices.foreach_get("select", select)
            co = np.empty(nverts * 3, dtype=np.float32)
            ob.data.vertices.foreach_get("co", co)
            co.shape = -1, 3
            selection = Vector(np.average(co[select], axis=0))
            if relative:
                selection -= Vector(getattr(ob.snapper, self.point + "_location"))
                selection = selection.normalized()
            setattr(ob.snapper, self.point + attr, selection)
        bpy.ops.object.mode_set(mode="EDIT")


class SNAPPER_OT_SetLocation(bpy.types.Operator, SetterMixin):
    bl_idname = "object.snapper_set_location"
    bl_label = "Set Location"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = "Set location of a snap point to average of selected vertices"

    def execute(self, context):
        self.set_attr_to_selected(context, "_location")
        return {"FINISHED"}


class SNAPPER_OT_SetDirection(bpy.types.Operator, SetterMixin):
    bl_idname = "object.snapper_set_direction"
    bl_label = "Set Direction"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = "Set direction of a snap point to average of selected vertices"

    def execute(self, context):
        self.set_attr_to_selected(context, "_direction", relative=True)
        return {"FINISHED"}


class SNAPPER_OT_SetDirectionToNormal(bpy.types.Operator, SetterMixin):
    bl_idname = "object.snapper_set_direction_to_normal"
    bl_label = "Set Direction to Normal"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = "Set direction along the normal of selected elements"

    def execute(self, context):
        ob = context.active_object
        bpy.ops.object.mode_set(mode="OBJECT")
        if ob.type == "CURVE":
            locations = []
            for spline in ob.data.splines:
                if spline.type == "BEZIER":
                    for point in spline.bezier_points:
                        if point.select_left_handle:
                            locations.append(point.handle_left)
                        if point.select_right_handle:
                            locations.append(point.handle_right)
                        if point.select_control_point:
                            locations.append(point.co)
            if len(locations) >= 3:
                new_direction = geometry.normal(locations)
            else:  # do nothing
                new_direction = -Vector(getattr(ob.snapper, self.point + "_direction"))
        if ob.type == "LATTICE":
            locations = [point.co for point in ob.data.points if point.select]
            if len(locations) >= 3:
                new_direction = geometry.normal(locations)
            else:  # do nothing
                new_direction = -Vector(getattr(ob.snapper, self.point + "_direction"))
        else:
            nfaces = len(ob.data.polygons)
            select = np.empty(nfaces, dtype=bool)
            ob.data.polygons.foreach_get("select", select)
            if np.count_nonzero(select) > 0:
                normal = np.empty(nfaces * 3, dtype=np.float32)
                ob.data.polygons.foreach_get("normal", normal)
            else:
                nverts = len(ob.data.vertices)
                select = np.empty(nverts, dtype=bool)
                ob.data.vertices.foreach_get("select", select)
                if np.count_nonzero(select) > 0:
                    normal = np.empty(nverts * 3, dtype=np.float32)
                    ob.data.vertices.foreach_get("normal", normal)
                else:
                    normal = np.array([1, 0, 0], dtype=np.float32)
                    select = [True]
            normal.shape = -1, 3
            new_direction = Vector(np.average(normal[select], axis=0)).normalized()

        old_direction = Vector(getattr(ob.snapper, self.point + "_direction"))
        if (new_direction - old_direction).length < 0.0001:
            new_direction = -new_direction
        setattr(ob.snapper, self.point + "_direction", new_direction)
        bpy.ops.object.mode_set(mode="EDIT")
        return {"FINISHED"}


class SNAPPER_OT_SetUp(bpy.types.Operator, SetterMixin):
    bl_idname = "object.snapper_set_up"
    bl_label = "Set Up"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = "Set up vector of a snap point to average of selected vertices"

    def execute(self, context):
        self.set_attr_to_selected(context, "_up", relative=True)
        return {"FINISHED"}


class SNAPPER_OT_Reset(bpy.types.Operator, SetterMixin):
    bl_idname = "object.snapper_reset"
    bl_label = "Reset"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = "Reset vectors of snap point to defaults"

    def execute(self, context):
        ob = context.active_object
        setattr(ob.snapper, self.point + "_location", Vector((0, 0, 0)))
        setattr(ob.snapper, self.point + "_direction", Vector((1, 0, 0)))
        setattr(ob.snapper, self.point + "_up", Vector((0, 0, 1)))
        return {"FINISHED"}


class SNAPPER_OT_CyclesAxes(bpy.types.Operator, SetterMixin):
    bl_idname = "object.snapper_cycleaxes"
    bl_label = "Cycle"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = "Cycle direction vectors of snap point through common presets"

    def execute(self, context):
        ob = context.active_object
        direction = Vector(getattr(ob.snapper, self.point + "_direction"))
        if direction == Vector((1, 0, 0)):
            setattr(ob.snapper, self.point + "_direction", Vector((-1, 0, 0)))
            setattr(ob.snapper, self.point + "_up", Vector((0, 0, 1)))
        elif direction == Vector((-1, 0, 0)):
            setattr(ob.snapper, self.point + "_direction", Vector((0, 1, 0)))
            setattr(ob.snapper, self.point + "_up", Vector((0, 0, 1)))
        elif direction == Vector((0, 1, 0)):
            setattr(ob.snapper, self.point + "_direction", Vector((0, -1, 0)))
            setattr(ob.snapper, self.point + "_up", Vector((0, 0, 1)))
        elif direction == Vector((0, -1, 0)):
            setattr(ob.snapper, self.point + "_direction", Vector((0, 0, 1)))
            setattr(ob.snapper, self.point + "_up", Vector((1, 0, 0)))
        elif direction == Vector((0, 0, 1)):
            setattr(ob.snapper, self.point + "_direction", Vector((0, 0, -1)))
            setattr(ob.snapper, self.point + "_up", Vector((1, 0, 0)))
        else:
            setattr(ob.snapper, self.point + "_direction", Vector((1, 0, 0)))
            setattr(ob.snapper, self.point + "_up", Vector((0, 0, 1)))
        return {"FINISHED"}


# extra points operators


class SetterMixinExtra:
    point: IntProperty(name="To (active)", default=0)

    @classmethod
    def poll(
        self, context
    ):  # we should probably limit this to just one selective/active object, and only a mesh object
        return context.mode in ("EDIT_MESH", "EDIT_CURVE", "EDIT_LATTICE")

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "point")

    def set_attr_to_selected(self, context, attr, relative=False):
        ob = context.active_object
        bpy.ops.object.mode_set(mode="OBJECT")
        if ob.type == "CURVE":
            location = Vector()
            n = 0
            for spline in ob.data.splines:
                if spline.type == "BEZIER":
                    for point in spline.bezier_points:
                        if point.select_left_handle:
                            location += point.handle_left
                            n += 1
                        if point.select_right_handle:
                            location += point.handle_right
                            n += 1
                        if point.select_control_point:
                            location += point.co
                            n += 1
            if n > 0:
                selection = location / n
                if relative:
                    selection -= Vector(getattr(ob.snappoints[self.point], "location"))
                    selection = selection.normalized()
                setattr(ob.snapper, self.point + attr, selection)
        elif ob.type == "LATTICE":
            npoints = len(ob.data.points)
            select = np.empty(npoints, dtype=bool)
            ob.data.points.foreach_get("select", select)
            co = np.empty(npoints * 3, dtype=np.float32)
            ob.data.points.foreach_get("co", co)
            co.shape = -1, 3
            selection = Vector(np.average(co[select], axis=0))
            if relative:
                selection -= Vector(getattr(ob.snappoints[self.point], "location"))
                selection = selection.normalized()
            setattr(ob.snapper, self.point + attr, selection)
        else:
            nverts = len(ob.data.vertices)
            select = np.empty(nverts, dtype=bool)
            ob.data.vertices.foreach_get("select", select)
            co = np.empty(nverts * 3, dtype=np.float32)
            ob.data.vertices.foreach_get("co", co)
            co.shape = -1, 3
            selection = Vector(np.average(co[select], axis=0))
            if relative:
                selection -= Vector(getattr(ob.snappoints[self.point], "location"))
                selection = selection.normalized()
            setattr(ob.snappoints[self.point], attr, selection)
        bpy.ops.object.mode_set(mode="EDIT")


class SNAPPER_OT_SetLocationExtra(bpy.types.Operator, SetterMixinExtra):
    bl_idname = "object.snapper_set_location_extra"
    bl_label = "Set Location"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = "Set location of a snap point to average of selected vertices"

    def execute(self, context):
        self.set_attr_to_selected(context, "location")
        return {"FINISHED"}


class SNAPPER_OT_SetDirectionExtra(bpy.types.Operator, SetterMixinExtra):
    bl_idname = "object.snapper_set_direction_extra"
    bl_label = "Set Direction"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = "Set direction of a snap point to average of selected vertices"

    def execute(self, context):
        self.set_attr_to_selected(context, "direction", relative=True)
        return {"FINISHED"}


class SNAPPER_OT_SetUpExtra(bpy.types.Operator, SetterMixinExtra):
    bl_idname = "object.snapper_set_up_extra"
    bl_label = "Set Up"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = "Set up vector of a snap point to average of selected vertices"

    def execute(self, context):
        self.set_attr_to_selected(context, "up", relative=True)
        return {"FINISHED"}


class SNAPPER_OT_ResetExtra(bpy.types.Operator, SetterMixinExtra):
    bl_idname = "object.snapper_reset_extra"
    bl_label = "Reset"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = "Reset vectors of snap point to defaults"

    def execute(self, context):
        ob = context.active_object
        setattr(ob.snappoints[self.point], "location", Vector((0, 0, 0)))
        setattr(ob.snappoints[self.point], "direction", Vector((1, 0, 0)))
        setattr(ob.snappoints[self.point], "up", Vector((0, 0, 1)))
        return {"FINISHED"}


class SNAPPER_OT_CyclesAxesExtra(bpy.types.Operator, SetterMixinExtra):
    bl_idname = "object.snapper_cycleaxes_extra"
    bl_label = "Cycle"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = "Cycle direction vectors of snap point through common presets"

    def execute(self, context):
        ob = context.active_object
        thepoint = ob.snappoints[self.point]
        direction = Vector(getattr(thepoint, "direction"))
        if direction == Vector((1, 0, 0)):
            setattr(thepoint, "direction", Vector((-1, 0, 0)))
            setattr(thepoint, "up", Vector((0, 0, 1)))
        elif direction == Vector((-1, 0, 0)):
            setattr(thepoint, "direction", Vector((0, 1, 0)))
            setattr(thepoint, "up", Vector((0, 0, 1)))
        elif direction == Vector((0, 1, 0)):
            setattr(thepoint, "direction", Vector((0, -1, 0)))
            setattr(thepoint, "up", Vector((0, 0, 1)))
        elif direction == Vector((0, -1, 0)):
            setattr(thepoint, "direction", Vector((0, 0, 1)))
            setattr(thepoint, "up", Vector((1, 0, 0)))
        elif direction == Vector((0, 0, 1)):
            setattr(thepoint, "direction", Vector((0, 0, -1)))
            setattr(thepoint, "up", Vector((1, 0, 0)))
        else:
            setattr(thepoint, "direction", Vector((1, 0, 0)))
            setattr(thepoint, "up", Vector((0, 0, 1)))
        return {"FINISHED"}


class SNAPPER_OT_SetDirectionToNormalExtra(bpy.types.Operator, SetterMixinExtra):
    bl_idname = "object.snapper_set_direction_to_normal_extra"
    bl_label = "Set Direction to Normal"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = "Set direction along the normal of selected elements"

    def execute(self, context):
        ob = context.active_object
        bpy.ops.object.mode_set(mode="OBJECT")
        if ob.type == "CURVE":
            locations = []
            for spline in ob.data.splines:
                if spline.type == "BEZIER":
                    for point in spline.bezier_points:
                        if point.select_left_handle:
                            locations.append(point.handle_left)
                        if point.select_right_handle:
                            locations.append(point.handle_right)
                        if point.select_control_point:
                            locations.append(point.co)
            if len(locations) >= 3:
                new_direction = geometry.normal(locations)
            else:  # do nothing
                new_direction = -Vector(getattr(ob.snappoints[self.point], "direction"))
        if ob.type == "LATTICE":
            locations = [point.co for point in ob.data.points if point.select]
            if len(locations) >= 3:
                new_direction = geometry.normal(locations)
            else:  # do nothing
                new_direction = -Vector(getattr(ob.snappoints[self.point], "direction"))
        else:
            nfaces = len(ob.data.polygons)
            select = np.empty(nfaces, dtype=bool)
            ob.data.polygons.foreach_get("select", select)
            if np.count_nonzero(select) > 0:
                normal = np.empty(nfaces * 3, dtype=np.float32)
                ob.data.polygons.foreach_get("normal", normal)
            else:
                nverts = len(ob.data.vertices)
                select = np.empty(nverts, dtype=bool)
                ob.data.vertices.foreach_get("select", select)
                if np.count_nonzero(select) > 0:
                    normal = np.empty(nverts * 3, dtype=np.float32)
                    ob.data.vertices.foreach_get("normal", normal)
                else:
                    normal = np.array([1, 0, 0], dtype=np.float32)
                    select = [True]
            normal.shape = -1, 3
            new_direction = Vector(np.average(normal[select], axis=0)).normalized()

        old_direction = Vector(getattr(ob.snappoints[self.point], "direction"))
        if (new_direction - old_direction).length < 0.0001:
            new_direction = -new_direction
        setattr(ob.snappoints[self.point], "direction", new_direction)
        bpy.ops.object.mode_set(mode="EDIT")
        return {"FINISHED"}


# general operators
class SNAPPER_OT_SnapEnable(bpy.types.Operator):
    bl_idname = "object.snapper_snapenable"
    bl_label = "Enable/Disable Snapper"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = "Make an object suitable for snapping"

    @classmethod
    def poll(self, context):
        return context.object is not None

    def draw(self, context):
        # we draw nothing
        pass

    def execute(self, context):
        context.active_object.snapper.snapper = (
            not context.active_object.snapper.snapper
        )
        scale = max(0.2, min(context.active_object.dimensions))
        for pt in POINTS:
            setattr(context.active_object.snapper, f"{pt}_gizmoscale", scale)
        # all kinds of hacks to cause the 3d view to redraw and show the new state
        context.scene.render.preview_pixel_size = (
            context.scene.render.preview_pixel_size
        )
        context.active_object.update_tag()
        context.scene.update_tag()
        context.view_layer.update()
        return {"FINISHED"}


class SnapModalMixin(bpy.types.Operator):
    """Snap an object interactively"""

    first_mouse: IntVectorProperty(size=2)
    first_value: FloatVectorProperty()

    # TODO set area header with some help text
    def modal(self, context, event):
        global from_point
        global to_point
        if event.type == "MOUSEMOVE":
            delta = Vector(
                (
                    self.first_mouse[0] - event.mouse_x,
                    self.first_mouse[1] - event.mouse_y,
                )
            )
            region = context.region
            rv3d = context.region_data
            context.object.location = view3d_utils.region_2d_to_location_3d(
                region, rv3d, self.obj2d - delta, context.object.location
            )

            mat = context.object.matrix_world

            from_point = None
            to_point = None

            shortest_distance = 1e30  # far, far away
            self.target_index = None
            self.target_point = None
            self.from_point = None
            # find closest pair of snappoints
            if self.match_tags:
                for pt, from_loc in self.snappoints.items():
                    from_loc_ws = mat @ from_loc
                    to_loc, index, distance = self.kd.find(from_loc_ws)
                    if (
                        distance is not None
                        and distance < 2
                        and distance < shortest_distance
                        and bool(
                            self.from_tags[pt].intersection(self.target_tags[index])
                        )
                    ):  # TODO make this limit configurable
                        to_point = to_loc
                        from_point = from_loc_ws
                        shortest_distance = distance
                        self.target_index = index
                        self.from_point = pt
            else:
                for pt, from_loc in self.snappoints.items():
                    from_loc_ws = mat @ from_loc
                    to_loc, index, distance = self.kd.find(from_loc_ws)
                    if (  # distance will be None if kd tree is empty
                        distance is not None
                        and distance < 2
                        and distance < shortest_distance
                    ):  # TODO make this limit configurable
                        to_point = to_loc
                        from_point = from_loc_ws
                        shortest_distance = distance
                        self.target_index = index
                        self.from_point = pt

        elif event.type == "LEFTMOUSE":
            if self.target_index is not None:
                if not event.shift:
                    # snap objects
                    if from_point is not None and to_point is not None:
                        align_objects(
                            self.target_obs[self.target_index][0],
                            context.object,
                            self.target_obs[self.target_index][1],
                            self.from_point,
                            flip=self.flip,
                        )
                # clear highlights
                from_point = None
                to_point = None
                # parent
                if context.preferences.addons[__name__].preferences.autoparent:
                    target = self.target_obs[self.target_index][0]
                    snapped = context.object
                    for oball in context.scene.objects:
                        oball.select_set(False)
                    snapped.select_set(True)
                    context.view_layer.objects.active = target
                    bpy.ops.object.parent_set()
                    context.view_layer.objects.active = snapped
            # all kinds of hacks to cause the 3d view to redraw and show the new state
            context.scene.render.preview_pixel_size = (
                context.scene.render.preview_pixel_size
            )
            context.active_object.update_tag()
            context.scene.update_tag()
            context.view_layer.update()
            return {"FINISHED"}

        elif event.type in {"RIGHTMOUSE", "ESC"}:
            # reset location of moved object
            context.object.location = self.first_value
            # clear highlights
            from_point = None
            to_point = None
            return {"CANCELLED"}

        return {"RUNNING_MODAL"}

    def invoke(self, context, event):
        global from_point
        global to_point

        self.match_tags = context.preferences.addons[__name__].preferences.matchtags

        # create a tree of all snap points world locations and their (snappoint, ob) tuples
        # first the 4 base points
        self.target_obs = [
            (ob, pt)
            for ob in context.view_layer.objects
            for pt in POINTS
            if (
                ob.snapper.snapper
                and not ob.hide_get()
                and not getattr(ob.snapper, f"{pt}_disable")
                and context.active_object != ob
            )
        ]
        # then the extra points (if any)
        self.target_obs.extend(
            [
                (ob, pt)
                for ob in context.view_layer.objects
                for pt in range(len(ob.snappoints))
                if (
                    ob.snapper.snapper
                    and not ob.hide_get()
                    and not ob.snappoints[pt].disable
                    and context.active_object != ob
                )
            ]
        )

        self.target_tags = {}
        self.kd = kdtree.KDTree(len(self.target_obs))
        for i, (ob, pt) in enumerate(self.target_obs):
            if type(pt) == str:
                self.kd.insert(
                    ob.matrix_world @ Vector(getattr(ob.snapper, f"{pt}_location")), i
                )
                tags = getattr(ob.snapper, f"{pt}_accepttags").strip()
                self.target_tags[i] = (
                    set(t.strip() for t in tags.split(",")) if len(tags) else set()
                )
            else:
                self.kd.insert(ob.matrix_world @ Vector(ob.snappoints[pt].location), i)
                tags = ob.snappoints[pt].accepttags.strip()
                self.target_tags[i] = (
                    set(t.strip() for t in tags.split(",")) if len(tags) else set()
                )
        self.kd.balance()

        # create a list of all snap points locations and their snappoints for the active object
        self.snappoints = {}
        self.from_tags = {}
        for pt in POINTS:
            location = getattr(context.object.snapper, f"{pt}_location")
            if not getattr(context.object.snapper, f"{pt}_disable"):
                self.snappoints[pt] = Vector(location)
                tags = getattr(context.object.snapper, f"{pt}_tags").strip()
                self.from_tags[pt] = (
                    set(t.strip() for t in tags.split(",")) if len(tags) else set()
                )
        for pt in range(len(context.object.snappoints)):
            p = context.object.snappoints[pt]
            location = p.location
            if not p.disable:
                self.snappoints[pt] = Vector(location)
                tags = p.tags.strip()
                self.from_tags[pt] = (
                    set(t.strip() for t in tags.split(",")) if len(tags) else set()
                )

        # initialize from_point and to_point to None
        from_point = None
        to_point = None
        if context.object:
            self.first_mouse = event.mouse_x, event.mouse_y
            self.first_value = context.object.location
            region = context.region
            rv3d = context.region_data
            self.obj2d = view3d_utils.location_3d_to_region_2d(
                region, rv3d, context.object.location
            )
            self.flip = context.preferences.addons[__name__].preferences.flip

            context.window_manager.modal_handler_add(self)
            return {"RUNNING_MODAL"}
        else:
            self.report({"WARNING"}, "No active object, could not finish")
            return {"CANCELLED"}


class SnapModalMixinSync(bpy.types.Operator):
    """Snap an active object interactively along with any other selected objects"""

    first_mouse: IntVectorProperty(size=2)
    first_value: FloatVectorProperty()

    # TODO set area header with some help text
    def modal(self, context, event):
        global from_point
        global to_point
        global parented_objects
        global already_parented_objects

        if event.type == "MOUSEMOVE":
            delta = Vector(
                (
                    self.first_mouse[0] - event.mouse_x,
                    self.first_mouse[1] - event.mouse_y,
                )
            )
            region = context.region
            rv3d = context.region_data
            context.object.location = view3d_utils.region_2d_to_location_3d(
                region, rv3d, self.obj2d - delta, context.object.location
            )

            mat = context.object.matrix_world

            from_point = None
            to_point = None

            shortest_distance = 1e30  # far, far away
            self.target_index = None
            self.target_point = None
            self.from_point = None
            # find closest pair of snappoints
            if self.match_tags:
                for pt, from_loc in self.snappoints.items():
                    from_loc_ws = mat @ from_loc
                    to_loc, index, distance = self.kd.find(from_loc_ws)
                    if (
                        distance is not None
                        and distance < 2
                        and distance < shortest_distance
                        and bool(
                            self.from_tags[pt].intersection(self.target_tags[index])
                        )
                    ):  # TODO make this limit configurable
                        to_point = to_loc
                        from_point = from_loc_ws
                        shortest_distance = distance
                        self.target_index = index
                        self.from_point = pt
            else:
                for pt, from_loc in self.snappoints.items():
                    from_loc_ws = mat @ from_loc
                    to_loc, index, distance = self.kd.find(from_loc_ws)
                    if (  # distance will be None if kd tree is empty
                        distance is not None
                        and distance < 2
                        and distance < shortest_distance
                    ):  # TODO make this limit configurable
                        to_point = to_loc
                        from_point = from_loc_ws
                        shortest_distance = distance
                        self.target_index = index
                        self.from_point = pt

        elif event.type == "LEFTMOUSE":
            if self.target_index is not None:
                if not event.shift:
                    # snap objects
                    if from_point is not None and to_point is not None:
                        align_objects(
                            self.target_obs[self.target_index][0],
                            context.object,
                            self.target_obs[self.target_index][1],
                            self.from_point,
                            flip=self.flip,
                        )
                # clear highlights
                from_point = None
                to_point = None
                snapped = context.object
                # parent
                if context.preferences.addons[__name__].preferences.autoparent:
                    target = self.target_obs[self.target_index][0]
                    for oball in context.scene.objects:
                        oball.select_set(False)
                    snapped.select_set(True)
                    context.view_layer.objects.active = target
                    bpy.ops.object.parent_set()
                    context.view_layer.objects.active = snapped
                # unparent any temporarily parented objects
                if len(parented_objects):
                    for oball in context.scene.objects:
                        oball.select_set(False)
                    for ob in parented_objects:
                        ob.select_set(True)
                    bpy.ops.object.parent_clear(type="CLEAR_KEEP_TRANSFORM")
                    if False:
                        # at this point any selected objects that were already parented will have shifted their parent to the snapped object
                        for ob, parent in already_parented_objects.items():
                            for oball in context.scene.objects:
                                oball.select_set(False)
                            for ob in already_parented_objects:
                                ob.select_set(True)
                            bpy.ops.object.parent_clear(type="CLEAR_KEEP_TRANSFORM")

                            for oball in context.scene.objects:
                                oball.select_set(False)
                            parent.select_set(True)
                            ob.select_set(True)
                            context.view_layer.objects.active = parent
                            bpy.ops.object.parent_set()

                # reset the pre snap selection situation
                context.view_layer.objects.active = snapped
                snapped.select_set(True)
                for ob in parented_objects:
                    ob.select_set(True)
                for ob in already_parented_objects:
                    ob.select_set(True)
                parented_objects = None
                already_parented_objects = None

            # all kinds of hacks to cause the 3d view to redraw and show the new state
            context.scene.render.preview_pixel_size = (
                context.scene.render.preview_pixel_size
            )
            context.active_object.update_tag()
            context.scene.update_tag()
            context.view_layer.update()
            return {"FINISHED"}

        elif event.type in {"RIGHTMOUSE", "ESC"}:
            # reset location of moved object
            context.object.location = self.first_value
            # clear highlights
            from_point = None
            to_point = None
            snapped = context.object
            # unparent any temporarily parented objects
            if len(parented_objects):
                for oball in context.scene.objects:
                    oball.select_set(False)
                for ob in parented_objects:
                    ob.select_set(True)
                bpy.ops.object.parent_clear(type="CLEAR_KEEP_TRANSFORM")
                if False:
                    # at this point any selected objects that were already parented will have shifted their parent to the snapped object
                    for ob, parent in already_parented_objects.items():
                        for oball in context.scene.objects:
                            oball.select_set(False)
                        for ob in already_parented_objects:
                            ob.select_set(True)
                        bpy.ops.object.parent_clear(type="CLEAR_KEEP_TRANSFORM")

                        for oball in context.scene.objects:
                            oball.select_set(False)
                        parent.select_set(True)
                        ob.select_set(True)
                        context.view_layer.objects.active = parent
                        bpy.ops.object.parent_set()

            # reset the pre snap selection situation
            context.view_layer.objects.active = snapped
            snapped.select_set(True)
            for ob in parented_objects:
                ob.select_set(True)
            for ob in already_parented_objects:
                ob.select_set(True)
            parented_objects = None
            already_parented_objects = None

            return {"CANCELLED"}

        return {"RUNNING_MODAL"}

    def invoke(self, context, event):
        global from_point
        global to_point
        global parented_objects
        global already_parented_objects

        self.match_tags = context.preferences.addons[__name__].preferences.matchtags

        # create a tree of all snap points world locations and their (snappoint, ob) tuples
        # but exclude active as well as selected objects
        # first the 4 base points
        self.target_obs = [
            (ob, pt)
            for ob in context.view_layer.objects
            for pt in POINTS
            if (
                ob.snapper.snapper
                and not ob.hide_get()
                and not getattr(ob.snapper, f"{pt}_disable")
                and context.active_object != ob
                and ob not in context.selected_objects
            )
        ]
        # then the extra points (if any)
        self.target_obs.extend(
            [
                (ob, pt)
                for ob in context.view_layer.objects
                for pt in range(len(ob.snappoints))
                if (
                    ob.snapper.snapper
                    and not ob.hide_get()
                    and not ob.snappoints[pt].disable
                    and context.active_object != ob
                    and ob not in context.selected_objects
                )
            ]
        )

        self.target_tags = {}
        self.kd = kdtree.KDTree(len(self.target_obs))
        for i, (ob, pt) in enumerate(self.target_obs):
            if type(pt) == str:
                self.kd.insert(
                    ob.matrix_world @ Vector(getattr(ob.snapper, f"{pt}_location")), i
                )
                tags = getattr(ob.snapper, f"{pt}_accepttags").strip()
                self.target_tags[i] = (
                    set(t.strip() for t in tags.split(",")) if len(tags) else set()
                )
            else:
                self.kd.insert(ob.matrix_world @ Vector(ob.snappoints[pt].location), i)
                tags = ob.snappoints[pt].accepttags.strip()
                self.target_tags[i] = (
                    set(t.strip() for t in tags.split(",")) if len(tags) else set()
                )
        self.kd.balance()

        # create a list of all snap points locations and their snappoints for the active object
        self.snappoints = {}
        self.from_tags = {}
        for pt in POINTS:
            location = getattr(context.object.snapper, f"{pt}_location")
            if not getattr(context.object.snapper, f"{pt}_disable"):
                self.snappoints[pt] = Vector(location)
                tags = getattr(context.object.snapper, f"{pt}_tags").strip()
                self.from_tags[pt] = (
                    set(t.strip() for t in tags.split(",")) if len(tags) else set()
                )
        for pt in range(len(context.object.snappoints)):
            p = context.object.snappoints[pt]
            location = p.location
            if not p.disable:
                self.snappoints[pt] = Vector(location)
                tags = p.tags.strip()
                self.from_tags[pt] = (
                    set(t.strip() for t in tags.split(",")) if len(tags) else set()
                )

        # initialize from_point and to_point to None
        from_point = None
        to_point = None
        if context.object:
            self.first_mouse = event.mouse_x, event.mouse_y
            self.first_value = context.object.location
            region = context.region
            rv3d = context.region_data
            self.obj2d = view3d_utils.location_3d_to_region_2d(
                region, rv3d, context.object.location
            )
            self.flip = context.preferences.addons[__name__].preferences.flip

            # parent any selected objects to the active object
            if context.preferences.addons[__name__].preferences.moveselected:
                parented_objects = [
                    ob
                    for ob in context.selected_objects
                    if ob != context.active_object and ob.parent is None
                ]
                already_parented_objects = {
                    ob: ob.parent
                    for ob in context.selected_objects
                    if ob != context.active_object and ob.parent is not None
                }
                for ob in already_parented_objects:
                    ob.select_set(False)
                if len(parented_objects):
                    bpy.ops.object.parent_set()
            else:
                parented_objects = []
                already_parented_objects = {}

            context.window_manager.modal_handler_add(self)
            return {"RUNNING_MODAL"}
        else:
            self.report({"WARNING"}, "No active object, could not finish")
            return {"CANCELLED"}


class SNAPPER_OT_SnapModal(SnapModalMixinSync):
    """Snap an object interactively"""

    bl_idname = "object.snapper_snapmodal"
    bl_label = "Snap"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(self, context):
        return (
            context.mode == "OBJECT"
            and context.object
            and context.object.snapper.snapper
        )

    def invoke(self, context, event):
        return super().invoke(context, event)


class SNAPPER_OT_SnapModalDup(SnapModalMixinSync):
    """Snap an object interactively after duplication"""

    bl_idname = "object.snapper_snapmodal_dup"
    bl_label = "Snap"
    bl_options = {"REGISTER", "UNDO"}

    duplicate: BoolProperty(
        name="Duplicate", description="Duplicate object before snapping", default=False
    )
    link: BoolProperty(
        name="Link",
        description="Make a linked duplicate before snapping",
        default=False,
    )

    @classmethod
    def poll(self, context):
        return (
            context.mode == "OBJECT"
            and context.object
            and context.object.snapper.snapper
        )

    def invoke(self, context, event):
        if self.duplicate:
            bpy.ops.object.duplicate(linked=self.link)
        return super().invoke(context, event)


class SNAPPER_OT_Copy(bpy.types.Operator):
    bl_idname = "object.snapper_copy"
    bl_label = "Copy snap points"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = "Copy snap points from active to selected"

    @classmethod
    def poll(
        self, context
    ):  # we should probably limit this to at least one selective/active object, and only a mesh object
        return context.active_object.snapper.snapper

    def draw(self, context):
        # we draw nothing
        pass

    def execute(self, context):
        snap_src = context.active_object.snapper
        for ob in context.selected_objects:
            if ob is not context.active_object:
                snap_dst = ob.snapper
                snap_dst.snapper = snap_src.snapper
                for point in POINTS:
                    for attr in (
                        "disable",
                        "location",
                        "direction",
                        "up",
                        "right",
                        "snapangle",
                        "label",
                        "labelcolor",
                        "gizmoscale",
                        "tags",
                        "accepttags",
                    ):
                        setattr(
                            snap_dst,
                            f"{point}_{attr}",
                            getattr(snap_src, f"{point}_{attr}"),
                        )
                for point in context.active_object.snappoints:
                    pt = ob.snappoints.add()
                    for attr in (
                        "disable",
                        "location",
                        "direction",
                        "up",
                        "right",
                        "snapangle",
                        "label",
                        "labelcolor",
                        "gizmoscale",
                        "tags",
                        "accepttags",
                    ):
                        setattr(pt, attr, getattr(point, attr))
        # all kinds of hacks to cause the 3d view to redraw and show the new state
        context.scene.render.preview_pixel_size = (
            context.scene.render.preview_pixel_size
        )
        context.active_object.update_tag()
        context.scene.update_tag()
        context.view_layer.update()
        return {"FINISHED"}


class SNAPPER_OT_Select(bpy.types.Operator):
    bl_idname = "object.snapper_select"
    bl_label = "Select neighbors"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = "Select neighbors of selected objects"

    all: BoolProperty(
        name="All",
        default=False,
        description="Select recursively (neighbors of neighbors of ...",
    )

    @classmethod
    def poll(self, context):
        return len(context.selected_objects) > 0

    def execute(self, context):
        while True:
            # create a tree of all (non-selected) snap points world locations and their (snappoint, ob) tuples
            self.target_obs = [
                (ob, pt)
                for ob in context.view_layer.objects
                for pt in POINTS
                if (
                    ob.snapper.snapper
                    and not ob.hide_get()
                    and not getattr(ob.snapper, f"{pt}_disable")
                    and ob not in context.selected_objects
                )
            ]
            self.target_obs.extend(
                [
                    (ob, pt)
                    for ob in context.view_layer.objects
                    for pt in range(len(ob.snappoints))
                    if (
                        ob.snapper.snapper
                        and not ob.hide_get()
                        and not ob.snappoints[pt].disable
                        and context.active_object != ob
                    )
                ]
            )
            if len(self.target_obs) < 1:
                break

            self.kd = kdtree.KDTree(len(self.target_obs))
            for i, (ob, pt) in enumerate(self.target_obs):
                if type(pt) == str:
                    self.kd.insert(
                        ob.matrix_world @ Vector(getattr(ob.snapper, f"{pt}_location")),
                        i,
                    )
                else:
                    self.kd.insert(
                        ob.matrix_world @ Vector(ob.snappoints[pt].location), i
                    )
            self.kd.balance()

            # create a list of all snap points locations and their snappoints for the selected objects
            self.snappoints = []
            for ob in context.selected_objects:
                if ob.snapper.snapper:
                    for pt in POINTS:
                        location = getattr(ob.snapper, f"{pt}_location")
                        if not getattr(ob.snapper, f"{pt}_disable"):
                            self.snappoints.append(ob.matrix_world @ Vector(location))
                    for pt in range(len(ob.snappoints)):
                        p = ob.snappoints[pt]
                        location = p.location
                        self.snappoints.append(ob.matrix_world @ Vector(location))

            # find overlapping snap-points
            new = 0
            for point in self.snappoints:
                for to_loc, index, distance in self.kd.find_range(point, 0.0001):
                    self.target_obs[index][0].select_set(True)
                    new += 1

            if not self.all or new == 0:
                break

        # all kinds of hacks to cause the 3d view to redraw and show the new state
        context.scene.render.preview_pixel_size = (
            context.scene.render.preview_pixel_size
        )
        context.active_object.update_tag()
        context.scene.update_tag()
        context.view_layer.update()
        return {"FINISHED"}


class SNAPPER_OT_Cursor(bpy.types.Operator):
    bl_idname = "object.snapper_cursor"
    bl_label = "Cursor to snap-point"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = "Snap 3d cursor to snap-point"

    point: EnumProperty(items=[(p, p, p) for p in POINTS], name="Point", default="A")

    @classmethod
    def poll(self, context):
        return context.object and context.object.snapper.snapper

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "point")

    def execute(self, context):
        ob = context.active_object
        context.scene.cursor.location = ob.matrix_world @ Vector(
            getattr(ob.snapper, f"{self.point}_location")
        )
        return {"FINISHED"}


class SNAPPER_OT_CursorExtra(bpy.types.Operator):
    bl_idname = "object.snapper_cursor_extra"
    bl_label = "Cursor to snap-point"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = "Snap 3d cursor to snap-point"

    point: IntProperty(name="Point", default=0)

    @classmethod
    def poll(self, context):
        return context.object and context.object.snapper.snapper

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "point")

    def execute(self, context):
        ob = context.active_object
        context.scene.cursor.location = ob.matrix_world @ Vector(
            ob.snappoints[self.point].location
        )
        return {"FINISHED"}


class SNAPPER_MT_Pie(Menu):
    bl_label = "Snap!"

    def draw(self, context):
        layout = self.layout.menu_pie()
        all_operators(layout, context)


km = None
ki = None


def add_shortcut():
    wm = bpy.context.window_manager
    kc = wm.keyconfigs

    global km
    global ki
    if km is None or ki is None:
        mapname = "3D View"
        if mapname in kc.addon.keymaps:
            km = kc.addon.keymaps[mapname]
        else:
            km = kc.addon.keymaps.new(mapname, space_type="VIEW_3D")
        ki = km.keymap_items.new("object.snapper_snapmodal", "K", "PRESS", ctrl=True)


def remove_shortcut():
    global km
    global ki
    if km and ki:
        km.keymap_items.remove(ki)
    km = None
    ki = None


def update_shortcut(self, context):
    if context.preferences.addons[__name__].preferences.shortcut:
        add_shortcut()
    else:
        remove_shortcut()


class SnapperPreferences(bpy.types.AddonPreferences):
    bl_idname = __name__

    visible: BoolProperty(
        name="Visible", description="Uncheck to hide all snappoints", default=True
    )
    shortcut: BoolProperty(
        name="Create Ctrl-K Shortcut",
        description="Create Ctrl-K shortcut for interactive snapping",
        default=False,
        update=update_shortcut,
    )
    fontsize: IntProperty(
        name="Fontsize",
        description="Fontsize for labels",
        default=50,
        min=2,
        soft_max=150,
    )
    fontshadow: BoolProperty(
        name="Drop shadow", description="Add a dropshadow to labels", default=True
    )
    labeloffset: IntVectorProperty(size=2, default=(4, 4))
    dircolor: FloatVectorProperty(
        name="Dir",
        size=4,
        default=(1, 0, 0, 1),
        description="Direction arrow color",
        subtype="COLOR",
    )
    upcolor: FloatVectorProperty(
        name="Up",
        size=4,
        default=(0, 0, 1, 1),
        description="Up arrow color",
        subtype="COLOR",
    )
    rightcolor: FloatVectorProperty(
        name="Right",
        size=4,
        default=(0, 1, 0, 1),
        description="Right arrow color",
        subtype="COLOR",
    )
    fromcolor: FloatVectorProperty(
        name="From",
        size=3,
        default=(0, 0, 1),
        description="Color of interactive snappoint",
        subtype="COLOR",
    )
    tocolor: FloatVectorProperty(
        name="To",
        size=3,
        default=(0, 1, 0),
        description="Color of interactive target",
        subtype="COLOR",
    )
    linewidth: IntProperty(
        name="Linewidth",
        description="Linewidth of arrows",
        default=1,
        min=1,
        soft_max=5,
    )
    conescale: FloatProperty(
        name="Head size",
        default=1.0,
        min=0.1,
        max=10.0,
        description="Size of arrowhead",
    )
    flip: BoolProperty(
        name="Auto flip", description="Automatically flip when snapping", default=False
    )
    coloroverride: BoolProperty(
        name="Override",
        description="Override color of labels defined in snap-points",
        default=False,
    )
    replacementcolor: FloatVectorProperty(
        name="To",
        size=4,
        default=(1, 1, 1, 1),
        description="Replacement color",
        subtype="COLOR",
    )
    autoparent: BoolProperty(
        name="Autoparent",
        description="Automatically parent an object after snapping",
        default=False,
    )
    moveselected: BoolProperty(
        name="Move selected",
        description="Move any additional selected objects along with the active object",
        default=True,
    )
    matchtags: BoolProperty(
        name="Match tags",
        description="Only snap points with matching tags",
        default=False,
    )

    debug: BoolProperty(
        name="Debug",
        description="Show debug options",
        default=False,
    )

    nocones: BoolProperty(
        name="Hide cones",
        description="Do not draw arrowheads in 3d display",
        default=False,
    )

    noarrows: BoolProperty(
        name="Hide arrows",
        description="Do not draw snappoint lines in 3d diaply",
        default=False,
    )

    dump: BoolProperty(
        name="Dump",
        description="Log transformation data of arrowheads to console (very verbose!)",
        default=False,
    )

    def draw(self, context):
        layout = self.layout
        row = layout.row()
        row.prop(self, "visible")
        row.prop(self, "shortcut")
        row = layout.row(heading="Labels")
        row.prop(self, "fontsize", text="Size")
        row.prop(self, "fontshadow", text="Shadow")
        row.prop(self, "labeloffset", text="Offset")
        row = layout.row(heading="Label color")
        row.prop(self, "coloroverride")
        row.prop(self, "replacementcolor", text="")
        row = layout.row()
        col1 = row.box().column(heading="Widget", align=True)
        col2 = row.box().column(heading="Highlight", align=True)
        col1.prop(self, "dircolor")
        col1.prop(self, "upcolor")
        col1.prop(self, "rightcolor")
        col1.separator()
        col1.prop(self, "linewidth")
        col1.prop(self, "conescale")
        col2.prop(self, "fromcolor")
        col2.prop(self, "tocolor")
        row = layout.row()
        col = row.box().column(heading="Behavior", align=True)
        col.prop(self, "flip")
        col.prop(self, "autoparent")
        col.prop(self, "moveselected")
        col.prop(self, "matchtags")
        row = layout.row()
        col = row.box().column(heading="Developer", align=True)
        col.prop(self, "debug")
        if self.debug:
            col.prop(self, "nocones")
            col.prop(self, "noarrows")
            col.prop(self, "dump")


classes = (
    SnapperPropertyGroup,
    SnapperPointPropertyGroup,
    SnapperPreferences,
    SNAPPER_PT_Operators,
    SNAPPER_PT_Snappoints,
    SNAPPER_PT_PointCollection,
    POINTS_UL_Snapper,
    SNAPPER_MT_Pie,
    SNAPPER_OT_SnapEnable,
    SNAPPER_OT_Snap,
    SNAPPER_OT_Rotate,
    SNAPPER_OT_RotateExtra,
    SNAPPER_OT_Flip,
    SNAPPER_OT_FlipExtra,
    SNAPPER_OT_SetLocation,
    SNAPPER_OT_SetLocationExtra,
    SNAPPER_OT_SetDirection,
    SNAPPER_OT_SetDirectionExtra,
    SNAPPER_OT_SetUp,
    SNAPPER_OT_SetUpExtra,
    SNAPPER_OT_Reset,
    SNAPPER_OT_ResetExtra,
    SNAPPER_OT_CyclesAxes,
    SNAPPER_OT_CyclesAxesExtra,
    SNAPPER_OT_SetDirectionToNormal,
    SNAPPER_OT_SetDirectionToNormalExtra,
    SNAPPER_OT_SnapModal,
    SNAPPER_OT_SnapModalDup,
    SNAPPER_OT_Copy,
    SNAPPER_OT_Select,
    SNAPPER_OT_Cursor,
    SNAPPER_OT_CursorExtra,
    SNAPPER_OT_PointAdd,
    SNAPPER_OT_PointRemove,
)


def register():
    global handler
    global label_handler
    global icons
    global from_point
    global to_point
    from_point = None
    to_point = None
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Object.snapper = bpy.props.PointerProperty(type=SnapperPropertyGroup)
    bpy.types.Object.snappoints = bpy.props.CollectionProperty(
        type=SnapperPointPropertyGroup
    )
    bpy.types.Object.active_snappoint = bpy.props.IntProperty(
        name="Active", default=0
    )  # , update=index_changed)
    icons = load_icons()
    handler = bpy.types.SpaceView3D.draw_handler_add(
        draw_handler_post_view, (), "WINDOW", "POST_VIEW"
    )
    label_handler = bpy.types.SpaceView3D.draw_handler_add(
        draw_handler_post_pixel, (), "WINDOW", "POST_PIXEL"
    )
    update_shortcut(None, bpy.context)


def unregister():
    global handler
    global label_handler
    global icons
    if handler is not None:
        bpy.types.SpaceView3D.draw_handler_remove(handler, "WINDOW")
    if label_handler is not None:
        bpy.types.SpaceView3D.draw_handler_remove(label_handler, "WINDOW")
    if icons:
        bpy.utils.previews.remove(icons)
    icons = None
    remove_shortcut()
    for c in classes:
        bpy.utils.unregister_class(c)
