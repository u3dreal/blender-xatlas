# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.


bl_info = {
	"name": "Blender Xatlas",
	"description": "Unwrap Objects with Xatlas, 'A cleaned up version of thekla_atlas'",
	"author": "mattedickson",
	"wiki_url": "https://github.com/mattedicksoncom",
	"tracker_url": "https://github.com/mattedicksoncom",
	"version": (0, 0, 1),
	"blender": (2, 83, 0),
	"location": "3D View > Toolbox",
	"category": "Object",
}

import os
import sys
import bpy
import bmesh
from random import random

from dataclasses import dataclass
from dataclasses import field
from typing import List

from io import StringIO
import struct

import subprocess
import threading
from threading  import Thread
from queue import Queue, Empty
import time
import string
import random

import pickle

# from export_obj_simple import save
# export_obj_simple = __import__(export_obj_simple
# import export_obj_simple

import importlib
sys.path.append(__path__)
from . import export_obj_simple

importlib.reload(export_obj_simple)


from bpy.utils import ( register_class, unregister_class )
from bpy.props import (
    StringProperty,
    BoolProperty,
    IntProperty,
    FloatProperty,
    FloatVectorProperty,
    EnumProperty,
    PointerProperty,
)
from bpy.types import (
    Panel,
    AddonPreferences,
    Operator,
    PropertyGroup,
)

addon_name = __name__

# begin utility functions---------------------------

#https://stackoverflow.com/questions/13484726/safe-enough-8-character-short-unique-random-string
def random_choice():
    alphabet = string.ascii_lowercase + string.digits
    return ''.join(random.choices(alphabet, k=8))

# end utility functions---------------------------


# begin PropertyGroups---------------------------
class PG_PackProperties (PropertyGroup):

    bruteForce : BoolProperty(
        name="Brute Force",
        description="Slower, but gives the best result. If false, use random chart placement.",
        default = False
        )

    resolution : IntProperty(
        name = "Texture Resolution (px)",
        description="Resolution of goal texture",
        default = 256,
        min = 0,
        max = 4096
        )

    padding : IntProperty(
        name = "Padding Amount (px)",
        description="Pixels to pad each uv island",
        default = 2,
        min = 0,
        max = 64
        )

    bilinear : BoolProperty(
        name="Bilinear",
        description="Leave space around pack for bilinear filtering",
        default = True
        )

    blockAlign : BoolProperty(
        name="blockAlign",
        description="Align charts to 4x4 blocks. Also improves packing speed, since there are fewer possible chart locations to consider.",
        default = False
        )

    maxChartSize : IntProperty(
        name = "maxChartSize",
        description="Charts larger than this will be scaled down. 0 means no limit.",
        default = 0,
        min = 0,
        max = 10000
        )

    texelsPerUnit : FloatProperty(
        name = "texelsPerUnit",
        description = "Unit to texel scale. e.g. a 1x1 quad with texelsPerUnit of 32 will take up approximately 32x32 texels in the atlas.\nIf resolution is also 0, the estimated value will approximately match a 1024x1024 atlas.",
        default = 0.0,
        min = 0.0,
        max = 10000.0
        )

class PG_ChartProperties (PropertyGroup):

    maxChartArea : FloatProperty(
        name = "maxChartArea",
        description = "Don't grow charts to be larger than this. 0 means no limit.",
        default = 0.0,
        min = 0.0,
        max = 10000.0
        )
    maxBoundaryLength : FloatProperty(
        name = "maxBoundaryLength",
        description = "Don't grow charts to have a longer boundary than this. 0 means no limit.",
        default = 0.0,
        min = 0.0,
        max = 10000.0
        )

    # Weights determine chart growth. Higher weights mean higher cost for that metric.
    normalDeviationWeight : FloatProperty(
        name = "normalDeviationWeight",
        description = "Angle between face and average chart normal.",
        default = 2.0,
        min = 0.0,
        max = 10000.0
        )
    roundnessWeight : FloatProperty(
        name = "roundnessWeight",
        description = "TODO",
        default = 0.01,
        min = 0.0,
        max = 10000.0
        )
    straightnessWeight : FloatProperty(
        name = "straightnessWeight",
        description = "TODO",
        default = 6.0,
        min = 0.0,
        max = 10000.0
        )
    normalSeamWeight : FloatProperty(
        name = "normalSeamWeight",
        description = "If > 1000, normal seams are fully respected.",
        default = 4.0,
        min = 0.0,
        max = 10000.0
        )
    textureSeamWeight : FloatProperty(
        name = "textureSeamWeight",
        description = "If > 1000, normal seams are fully respected.",
        default = 0.5,
        min = 0.0,
        max = 10000.0
        )

    maxCost : FloatProperty(
        name = "maxCost",
        description = "If total of all metrics * weights > maxCost, don't grow chart. Lower values result in more charts.",
        default = 2.0,
        min = 0.0,
        max = 10000.0
        )

    maxIterations : IntProperty(
        name = "maxIterations",
        description="Number of iterations of the chart growing and seeding phases. Higher values result in better charts.",
        default = 1,
        min = 0,
        max = 1000
        )

# end PropertyGroups---------------------------

# begin operators------------------------------
#Unwrap Lightmap Group Xatlas
class Unwrap_Lightmap_Group_Xatlas_2(bpy.types.Operator):
    bl_idname = "object.unwrap_lightmap_group_xatlas_2"
    bl_label = "Unwrap Lightmap Group Xatlas"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):

        #get all the options for xatlas
        packOptions = bpy.context.scene.pack_tool
        chartOptions = bpy.context.scene.chart_tool

        #save whatever mode the user was in
        startingMode = bpy.context.object.mode

        #get all the currently selected objects
        selected_objects = bpy.context.selected_objects

        #check something is actually selected
        #external function/operator will select them
        if len(selected_objects) == 0:
            print("Nothing Selected")
            return {'FINISHED'}

        #store the names of objects
        rename_dict = dict();
        
        #make sure all the objects have ligthmap uvs
        for obj in selected_objects:
            if obj.type == 'MESH':
                rename_dict[obj.name] = obj.name
                context.view_layer.objects.active = obj
                uv_layers = obj.data.uv_layers
                if not "UVMap_Lightmap" in uv_layers:
                    uvmap = uv_layers.new(name="UVMap_Lightmap")
                    uv_layers.active_index = len(uv_layers) - 1
                else:
                    for i in range(0, len(uv_layers)):
                        if uv_layers[i].name == 'UVMap_Lightmap':
                            uv_layers.active_index = i
                obj.select_set(True)


        #Convert to tris
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.select_all(action='SELECT')
        bpy.ops.mesh.quads_convert_to_tris(quad_method='FIXED', ngon_method='BEAUTY')
        bpy.ops.object.mode_set(mode='OBJECT')

        #Create a fake obj export to a string
        #Will strip this down further later
        fakeFile = StringIO()
        export_obj_simple.save(
            context=bpy.context,
            filepath=fakeFile,
            use_selection=True,
            use_animation=False,
            use_mesh_modifiers=True,
            use_edges=True,
            use_smooth_groups=False,
            use_smooth_groups_bitflags=False, 
            use_normals=True,
            use_uvs=True,
            use_materials=False,
            use_triangles=False,
            use_nurbs=False, 
            use_vertex_groups=False, 
            use_blen_objects=True,
            group_by_object=False,
            group_by_material=False,
            keep_vertex_order=False,
        )

        #print just for reference
        print(fakeFile.getvalue())

        #get the path to xatlas
        file_path = os.path.dirname(os.path.abspath(__file__))
        xatlas_path = os.path.join(file_path, "xatlas", "xatlas-blender.exe")

        #setup the arguments to be passed to xatlas-------------------
        arguments_string = ""
        for argumentKey in packOptions.__annotations__.keys():
            key_string = str(argumentKey)
            if argumentKey is not None:
                print(getattr(packOptions,key_string))
                attrib = getattr(packOptions,key_string)
                if type(attrib) == bool:
                    if attrib == True:
                        arguments_string = arguments_string + " -" + str(argumentKey)
                else:
                    arguments_string = arguments_string + " -" + str(argumentKey) + " " + str(attrib)

        for argumentKey in chartOptions.__annotations__.keys():
            if argumentKey is not None:
                key_string = str(argumentKey)
                print(getattr(chartOptions,key_string))
                attrib = getattr(chartOptions,key_string)
                if type(attrib) == bool:
                    if attrib == True:
                        arguments_string = arguments_string + " -" + str(argumentKey)
                else:
                    arguments_string = arguments_string + " -" + str(argumentKey) + " " + str(attrib)

        print(arguments_string)
        #END setup the arguments to be passed to xatlas-------------------

        #RUN xatlas process
        xatlas_process = subprocess.Popen(
            xatlas_path + ' ' + arguments_string,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE
        )

        #shove the fake file in stdin
        stdin = xatlas_process.stdin
        value = bytes(fakeFile.getvalue() + "\n", 'UTF-8') #The \n is needed to end the input properly
        stdin.write(value)
        stdin.flush()

        #Get the output from xatlas
        outObj = ""
        while True:
            output = xatlas_process.stdout.readline()
            if not output:
                 break
            outObj = outObj + (output.decode().strip() + "\n")

        print(outObj)


        #Setup for reading the output
        @dataclass
        class uvObject:
            obName: string = ""
            uvArray: List[float] = field(default_factory=list)
            faceArray: List[int] = field(default_factory=list)
        
        convertedObjects = []
        uvArrayComplete = []

        
        #search through the out put for STARTOBJ
        #then start reading the objects
        obTest = None
        startRead = False
        for line in outObj.splitlines():
            
            line_split = line.split()

            if not line_split:
                continue

            line_start = line_split[0]  # we compare with this a _lot_
            # print(line_start)
            if line_start == "STARTOBJ":
                print("Start reading the objects----------------------------------------")
                startRead = True
                # obTest = uvObject()
            
            if startRead:
                #if it's a new obj
                if line_start == 'o':
                    #if there is already an object append it
                    if obTest is not None:
                        convertedObjects.append(obTest)
                    
                    obTest = uvObject() #create new uv object
                    obTest.obName = line_split[1]

                if obTest is not None:
                    #the uv coords
                    if line_start == 'vt':
                        newUv = [float(line_split[1]),float(line_split[2])]
                        obTest.uvArray.append(newUv)
                        uvArrayComplete.append(newUv)

                    #the face coords index
                    #faces are 1 indexed
                    if line_start == 'f':
                        #vert/uv/normal
                        #only need the uvs
                        newFace = [
                            int(line_split[1].split("/")[1]),
                            int(line_split[2].split("/")[1]),
                            int(line_split[3].split("/")[1])
                        ]
                        obTest.faceArray.append(newFace)

        #append the final object
        convertedObjects.append(obTest)
        # print(convertedObjects)
        
        
        #apply the output-------------------------------------------------------------
        #copy the uvs to the original objects
        # objIndex = 0
        print("Applying the UVs----------------------------------------")
        for importObject in convertedObjects:
            bpy.ops.object.select_all(action='DESELECT')

            obTest = importObject

            bpy.context.scene.objects[obTest.obName].select_set(True)
            context.view_layer.objects.active = bpy.context.scene.objects[obTest.obName]
            bpy.ops.object.mode_set(mode = 'OBJECT')

            obj = bpy.context.active_object
            me = obj.data
            #convert to bmesh to create the new uvs
            bm = bmesh.new()
            bm.from_mesh(me)

            uv_layer = bm.loops.layers.uv.verify()

            nFaces = len(bm.faces)
            #need to ensure lookup table for some reason?
            if hasattr(bm.faces, "ensure_lookup_table"): 
                bm.faces.ensure_lookup_table()

            #loop through the faces
            for faceIndex in range(nFaces):
                faceGroup = obTest.faceArray[faceIndex]

                bm.faces[faceIndex].loops[0][uv_layer].uv = (
                    uvArrayComplete[faceGroup[0] - 1][0],
                    uvArrayComplete[faceGroup[0] - 1][1])

                bm.faces[faceIndex].loops[1][uv_layer].uv = (
                    uvArrayComplete[faceGroup[1] - 1][0],
                    uvArrayComplete[faceGroup[1] - 1][1])

                bm.faces[faceIndex].loops[2][uv_layer].uv = (
                    uvArrayComplete[faceGroup[2] - 1][0],
                    uvArrayComplete[faceGroup[2] - 1][1])

                # objIndex = objIndex + 3

            # print(objIndex)
            #assign the mesh back to the original mesh
            bm.to_mesh(me)
        #END apply the output-------------------------------------------------------------

        #select the original objects that were selected
        for objectName in rename_dict:
            if objectName in bpy.context.scene.objects:
                current_object = bpy.context.scene.objects[objectName]
                current_object.select_set(True)
                context.view_layer.objects.active = current_object
        
        bpy.ops.object.mode_set(mode=startingMode)

        print("Finished Xatlas----------------------------------------")
        return {'FINISHED'}

# end operators------------------------------


# begin panels------------------------------
class OBJECT_PT_xatlas_panel (Panel):
    bl_idname = "OBJECT_PT_xatlas_panel"
    bl_label = "Xatlas Tools"
    bl_space_type = "VIEW_3D"   
    bl_region_type = "UI"
    bl_category = "Tool"
    bl_context = ""

    @classmethod
    def poll(self,context):
        return context.object is not None

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        packtool = scene.pack_tool
        mytool = scene.chart_tool

        #add the pack options
        box = layout.box()
        label = box.label(text="Pack Options")
        for tool in packtool.__annotations__.keys():
            box.prop( packtool, tool)

        #add the chart options
        box = layout.box()
        label = box.label(text="Chart Options")
        for tool in mytool.__annotations__.keys():
            box.prop( mytool, tool)

        box = layout.box()
        label = box.label(text="Run")
        box.operator("object.unwrap_lightmap_group_xatlas_2", text="Run Xatlas")

# end panels------------------------------





# begin setup------------------------------
# Import Operators
#from .export_obj_simple import CONSOLE_OBJ_2

classes = (
    PG_PackProperties,
    PG_ChartProperties,
    Unwrap_Lightmap_Group_Xatlas_2,
    OBJECT_PT_xatlas_panel,
)

def register():
    #
    for cls in classes:
        register_class(cls)
    #

    # bpy.types.Scene.save_obj_console = PointerProperty(type=CONSOLE_OBJ_2)
    bpy.types.Scene.pack_tool = PointerProperty(type=PG_PackProperties)
    bpy.types.Scene.chart_tool = PointerProperty(type=PG_ChartProperties)

    

    #

def unregister():
    #
    for cls in reversed(classes):
        unregister_class(cls)
    #

    
    del bpy.types.Scene.chart_tool
    del bpy.types.Scene.pack_tool
    # del bpy.types.Scene.save_obj_console
    




if __name__ == "__main__":
    pass
    #register()
# end setup------------------------------