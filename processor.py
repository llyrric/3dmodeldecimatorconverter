import bpy
import os
import sys
import argparse
import struct
import json
import tempfile
import shutil
import zipfile

def clear_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)

def clean_glb(filepath):
    """
    Unpacks GLB into a separate .gltf and .bin file to bypass Blender importer crashes.
    Also strips custom attributes, extras, animations, and skins.
    Returns the path to the temporary .gltf file.
    """
    if not filepath.lower().endswith('.glb'):
        return filepath
        
    try:
        temp_dir = tempfile.TemporaryDirectory()
        base = os.path.join(temp_dir.name, os.path.basename(filepath) + ".unpacked")
        gltf_path = base + ".gltf"
        bin_filename = os.path.basename(base) + ".bin"
        bin_path = os.path.join(temp_dir.name, bin_filename)

        with open(filepath, 'rb') as f:
            # Header
            magic = f.read(4)
            if magic != b'glTF':
                return filepath
            version = struct.unpack('<I', f.read(4))[0]
            f.read(4) # total_length

            # JSON Chunk
            chunk_len = struct.unpack('<I', f.read(4))[0]
            chunk_type = f.read(4)
            if chunk_type != b'JSON':
                return filepath
            
            json_bytes = f.read(chunk_len).strip(b'\x00\x20')
            data = json.loads(json_bytes.decode('utf-8'))

            # BIN Chunk
            bin_chunk_len = struct.unpack('<I', f.read(4))[0]
            bin_type = f.read(4)
            if bin_type != b'BIN\x00':
                bin_data = b''
            else:
                bin_data = f.read(bin_chunk_len)
            
        found_attributes = set()
        modified = False
        def clean_recursive(obj, key=None):
            nonlocal modified
            if isinstance(obj, dict):
                to_remove = []
                for k in list(obj.keys()):
                    if key == 'attributes':
                        found_attributes.add(k)
                    
                    # CRITICAL: Preserve _SURFACE_PROPS and COLOR_0
                    # Handle variants like __SURFACE_PROPS caused by Blender join
                    if k.startswith('_') and 'SURFACE_PROPS' not in k.upper():
                        to_remove.append(k)
                    elif k in ['extras', 'animations', 'skins', 'cameras']:
                        # Keep extras if it might contain MiniPainterDecal, but strip for stability
                        # Actually, keeping extras for objects might be useful for decals.
                        # For now, stay safe and only keep what we know we need.
                        to_remove.append(k)
                    else:
                        clean_recursive(obj[k], k)
                if to_remove:
                    for k in to_remove:
                        del obj[k]
                    modified = True
            elif isinstance(obj, list):
                for item in obj:
                    clean_recursive(item)

        clean_recursive(data)
        print(f"Found attributes in GLB: {', '.join(sorted(list(found_attributes)))}")
        
        # Ensure buffers exist
        if 'buffers' not in data:
            data['buffers'] = [{}]
        
        # Update buffer reference to the external .bin file
        if 'buffers' in data and len(data['buffers']) > 0:
            data['buffers'][0]['uri'] = bin_filename
            data['buffers'][0]['byteLength'] = len(bin_data)

        print(f"Unpacked and cleaned GLB to {os.path.basename(gltf_path)}")
        
        with open(gltf_path, 'w', encoding='utf-8') as f:
            json.dump(data, f)
            
        if bin_data:
            with open(bin_path, 'wb') as f:
                f.write(bin_data)
                
        return gltf_path, temp_dir
        
    except Exception as e:
        print(f"Warning: Could not unpack/clean GLB: {e}")
        import traceback
        traceback.print_exc()
        return filepath, None

def import_model(filepath):
    """
    Imports a 3D model, separates base meshes from decals, joins and welds the base.
    Returns: (welded_high_poly, decals, surface_props_attr, target_col_attr)
    """
    ext = os.path.splitext(filepath)[1].lower()
    
    # Deselect all before import to easily find the new object
    bpy.ops.object.select_all(action='DESELECT')
    
    clean_path = filepath
    try:
        if ext == '.obj':
            bpy.ops.wm.obj_import(filepath=filepath)
        elif ext == '.fbx':
            bpy.ops.import_scene.fbx(filepath=filepath)
        if ext in ['.gltf', '.glb']:
            temp_import_dir = None
            if ext == '.glb':
                clean_path, temp_import_dir = clean_glb(filepath)
            
            # Revert to default settings now that clean_glb handles the attribute issue
            # This is safer for Blender 5.1's internal state
            print(f"Starting GLTF import for: {clean_path}")
            bpy.ops.import_scene.gltf(filepath=clean_path)
            print("GLTF import operator finished."); sys.stdout.flush()
            
            # Force an update of the dependency graph and view layer
            # This can prevent access violations when accessing data immediately after import
            print("Updating view layer...")
            bpy.context.view_layer.update()
            print("View layer updated."); sys.stdout.flush()
            
        all_objs = [obj for obj in bpy.context.scene.objects if obj.type == 'MESH' and obj.name != "HighPoly"]
        if not all_objs:
            raise RuntimeError("No mesh found in imported file.")
            
        # 1. Identify Decals vs Base Meshes
        base_meshes = []
        decals = []
        
        for obj in all_objs:
            name_low = obj.name.lower()
            if any(k in name_low for k in ["decal", "sticker", "eye", "glass", "lens"]):
                print(f" - Identified decal object: {obj.name}")
                decals.append(obj)
            else:
                base_meshes.append(obj)
        
        if not base_meshes and decals:
            decals.sort(key=lambda o: len(o.data.vertices), reverse=True)
            base_meshes = [decals.pop(0)]
            print(f" - Fallback: Reassigned {base_meshes[0].name} as base mesh.")
            
        # 2. Join and Weld Base Meshes (The "Un-Shredder")
        high_poly = None
        if base_meshes:
            print(f"Joining {len(base_meshes)} base meshes...")
            bpy.ops.object.select_all(action='DESELECT')
            for obj in base_meshes:
                obj.select_set(True)
            bpy.context.view_layer.objects.active = base_meshes[0]
            bpy.ops.object.join()
            high_poly = bpy.context.active_object
            high_poly.name = "HighPoly_WeldedBase"
            
            # Weld the vertices (Merge by Distance) to heal the shredder seams
            print("Welding base mesh vertices (Merge by Distance)...")
            bpy.ops.object.mode_set(mode='EDIT')
            bpy.ops.mesh.select_all(action='SELECT')
            bpy.ops.mesh.remove_doubles(threshold=0.0001)
            bpy.ops.object.mode_set(mode='OBJECT')
            
            # Clear custom normals and shade smooth
            if high_poly.data.has_custom_normals:
                bpy.ops.object.mode_set(mode='EDIT')
                bpy.ops.mesh.customdata_custom_splitnormals_clear()
                bpy.ops.object.mode_set(mode='OBJECT')
            high_poly.select_set(True)
            bpy.ops.object.shade_smooth()
            
        # 3. Handle Decals (Shade Smooth)
        for d in decals:
            d.select_set(True)
            bpy.context.view_layer.objects.active = d
            bpy.ops.object.shade_smooth()
            # Also clear custom normals for decals to ensure good baking
            if d.data.has_custom_normals:
                bpy.ops.object.mode_set(mode='EDIT')
                bpy.ops.mesh.customdata_custom_splitnormals_clear()
                bpy.ops.object.mode_set(mode='OBJECT')

        # Find the best attribute for color
        primary_obj = high_poly if high_poly else (decals[0] if decals else None)
        surface_props_attr = None
        target_col_attr = None
        
        if primary_obj:
            all_attr_names = [a.name for a in primary_obj.data.attributes]
            color_attrs = [col.name for col in primary_obj.data.color_attributes]
            
            # Find SURFACE_PROPS
            sp_variants = [n for n in all_attr_names if 'SURFACE_PROPS' in n.upper()]
            if sp_variants:
                surface_props_attr = sorted(sp_variants, key=len, reverse=True)[0]
                print(f"Selected SURFACE_PROPS: {surface_props_attr}")
                
            # Find COLOR
            for name in ['COLOR_0', 'Color', 'Col', 'color']:
                if name in color_attrs:
                    target_col_attr = name
                    break
            if not target_col_attr:
                for name in color_attrs:
                    if name.lower() not in ['position']:
                        target_col_attr = name
                        break
        
        if target_col_attr:
            print(f"Selected color attribute for baking: '{target_col_attr}'")
        
        # Robust check for SURFACE_PROPS and EDGEGRADIENT variants
        # We find identifying keywords in all attributes
        surface_props_attr = None
        edge_gradient_attr = None
        
        all_attr_names = [a.name for a in high_poly.data.attributes]
        sp_variants = [n for n in all_attr_names if 'SURFACE_PROPS' in n.upper()]
        eg_variants = [n for n in all_attr_names if 'EDGEGRADIENT' in n.upper()]
        
        if sp_variants:
            surface_props_attr = sorted(sp_variants, key=len, reverse=True)[0]
            print(f"Selected primary SURFACE_PROPS: {surface_props_attr}")
        if eg_variants:
            edge_gradient_attr = sorted(eg_variants, key=len, reverse=True)[0]
            print(f"Selected primary EDGEGRADIENT: {edge_gradient_attr}")
        
        # Ensure high poly has at least one material for baking (albedo/color)
        if not high_poly.data.materials:
            mat = bpy.data.materials.new(name="HighPolyDefault")
            high_poly.data.materials.append(mat)
            print("Created fallback material for HighPoly.")
        
        # --- Setup HighPoly Material for Baking ---
        for mat in high_poly.data.materials:
            if mat is None: continue
            mat.use_nodes = True
            nodes = mat.node_tree.nodes
            links = mat.node_tree.links
            
            # Find Principled BSDF
            principled = next((n for n in nodes if n.type == 'BSDF_PRINCIPLED'), None)
            if not principled:
                principled = nodes.new('ShaderNodeBsdfPrincipled')
                output = next((n for n in nodes if n.type == 'OUTPUT_MATERIAL'), None)
                if not output:
                    output = nodes.new('ShaderNodeOutputMaterial')
                links.new(principled.outputs['BSDF'], output.inputs['Surface'])
            
            # Link Color
            if target_col_attr:
                attr_node = next((n for n in nodes if n.type == 'ATTRIBUTE' and n.attribute_name == target_col_attr), None)
                if not attr_node:
                    attr_node = nodes.new('ShaderNodeAttribute')
                    attr_node.attribute_name = target_col_attr
                    attr_node.location = (principled.location.x - 300, principled.location.y)
                links.new(attr_node.outputs['Color'], principled.inputs['Base Color'])

            # Link Surface Properties (Roughness, Metallic, Sheen)
            if surface_props_attr:
                # Use a specific attribute node for surface props
                sp_node = next((n for n in nodes if n.type == 'ATTRIBUTE' and n.attribute_name == surface_props_attr), None)
                if not sp_node:
                    sp_node = nodes.new('ShaderNodeAttribute')
                    sp_node.attribute_name = surface_props_attr
                    sp_node.location = (principled.location.x - 600, principled.location.y - 300)
                
                # Separate RGB/XYZ (X=Rough, Y=Metal, Z=Sheen)
                sep_node = nodes.new('ShaderNodeSeparateColor')
                sep_node.location = (sp_node.location.x + 200, sp_node.location.y)
                links.new(sp_node.outputs['Color'], sep_node.inputs['Color'])
                
                links.new(sep_node.outputs['Red'], principled.inputs['Roughness'])
                links.new(sep_node.outputs['Green'], principled.inputs['Metallic'])
                
                # Sheen Weight in Blender 4.2+ is Principled BSDF -> Sheen -> Weight
                # In some versions it might be separate. We'll try to find 'Sheen Weight'
                sheen_input = principled.inputs.get('Sheen Weight')
                if not sheen_input:
                    # Fallback for older Principled BSDF
                    sheen_input = principled.inputs.get('Sheen')
                
                if sheen_input:
                    links.new(sep_node.outputs['Blue'], sheen_input)
            
            # Fallback PBR values
            if not surface_props_attr:
                if principled.inputs['Roughness'].is_linked == False:
                    principled.inputs['Roughness'].default_value = 1.0
                if principled.inputs['Metallic'].is_linked == False:
                    principled.inputs['Metallic'].default_value = 0.0
        
        if target_col_attr:
            print(f"Selected color attribute: {target_col_attr}")

        return high_poly, decals, surface_props_attr, target_col_attr
        
    finally:
        # Cleanup cleaned file if one was created
        if clean_path != filepath and os.path.exists(clean_path):
            try:
                os.remove(clean_path)
                # Also remove the .bin file if it exists
                bin_path = clean_path.replace(".gltf", ".bin")
                if os.path.exists(bin_path):
                    os.remove(bin_path)
            except: pass

def create_emissive_attribute_material(obj, attribute_name):
    """
    Creates a material that outputs raw vertex attribute data as glowing light (Emission).
    Ensures that if the attribute is a Vector (X,Y,Z), it is correctly mapped to RGB.
    """
    mat_name = f"Mat_Bake_{attribute_name}"
    mat = bpy.data.materials.get(mat_name)
    if not mat:
        mat = bpy.data.materials.new(name=mat_name)
    
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()

    # Create Attribute Node
    attr_node = nodes.new(type="ShaderNodeAttribute")
    attr_node.attribute_name = attribute_name
    
    # Create Emission Node
    emit_node = nodes.new(type="ShaderNodeEmission")
    
    # Create Output Node
    output_node = nodes.new(type="ShaderNodeOutputMaterial")
    
    # MIX LOGIC: Blender handles Vector/Color outputs differently! 
    # To be ROBUST, we link the Color output directly.
    # If the attribute is a FLOAT_VECTOR, Blender's Attribute node automatically 
    # populates the 'Color' output with the X, Y, Z components as R, G, B.
    links.new(attr_node.outputs['Color'], emit_node.inputs['Color'])
    links.new(emit_node.outputs['Emission'], output_node.inputs['Surface'])
    
    # Apply to object
    obj.data.materials.clear()
    obj.data.materials.append(mat)
    return mat

def bake_pure_data_pass(high_poly_objs, low_poly, attribute_name, res_w, res_h, output_path):
    """
    Bakes the vertex attribute from high poly (Base + Decals) to an image texture on the low poly.
    """
    print(f"Prepping bakes for attribute: {attribute_name}...")
    
    # 1. Setup Emission Material on all high-poly objects
    for hp in high_poly_objs:
        create_emissive_attribute_material(hp, attribute_name)
        
    # 2. Setup Low Poly receiving image
    img_name = f"Bake_{attribute_name}"
    if img_name in bpy.data.images:
        bpy.data.images.remove(bpy.data.images[img_name])
    img = bpy.data.images.new(img_name, width=res_w, height=res_h)
    
    # Use the existing bake material/node on low poly
    mat = low_poly.data.materials[0]
    bake_node = mat.node_tree.nodes.get("BAKE_TARGET")
    if not bake_node:
        bake_node = mat.node_tree.nodes.new('ShaderNodeTexImage')
        bake_node.name = "BAKE_TARGET"
    
    bake_node.image = img
    mat.node_tree.nodes.active = bake_node
    
    # 3. Select High, then Low (Select ALL high poly sources)
    bpy.ops.object.select_all(action='DESELECT')
    for hp in high_poly_objs:
        hp.select_set(True)
    low_poly.select_set(True)
    bpy.context.view_layer.objects.active = low_poly
    
    # 4. Bake (Type = EMIT)
    bpy.context.scene.render.engine = 'CYCLES'
    bpy.context.scene.render.bake.use_selected_to_active = True
    bpy.context.scene.render.bake.margin = 16
    # Reaches out to grab the floating decals! 0.05 units is ample for 0.005 offset.
    bpy.context.scene.render.bake.cage_extrusion = 0.05
    
    print(f"Starting bake cycle for {attribute_name}...")
    bpy.ops.object.bake(type='EMIT')
    
    # 5. Save the image
    if output_path:
        img.filepath_raw = output_path
        img.file_format = 'PNG'
        img.save()
        print(f"Saved bake to {output_path}")
    
    return img


def decimate_mesh(obj, target_triangles):
    """
    Decimates a mesh to reach a target triangle count.
    """
    # Count current triangles
    print("Evaluating mesh for triangle count..."); sys.stdout.flush()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    mesh_eval = obj.evaluated_get(depsgraph).data
    current_triangles = len(mesh_eval.polygons)
    print(f"Current polygons: {current_triangles}"); sys.stdout.flush()
    
    # If the mesh has quads/ngons, we should triangulate first to get accurate counts
    print("Triangulating..."); sys.stdout.flush()
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.modifier_add(type='TRIANGULATE')
    bpy.ops.object.modifier_apply(modifier="Triangulate")
    print("Triangulation applied."); sys.stdout.flush()
    
    current_triangles = len(obj.data.polygons)
    print(f"Current triangle count: {current_triangles}"); sys.stdout.flush()
    
    if current_triangles <= target_triangles:
        print("Model is already below target triangle count. Skipping decimation.")
        return 1.0
        
    ratio = target_triangles / current_triangles
    print(f"Applying decimation with ratio: {ratio:.4f}")
    
    modifier = obj.modifiers.new(name="Decimate", type='DECIMATE')
    modifier.ratio = ratio
    modifier.use_collapse_triangulate = True
    print("Applying decimation modifier..."); sys.stdout.flush()
    bpy.ops.object.modifier_apply(modifier="Decimate")
    print("Decimation applied."); sys.stdout.flush()
    bpy.context.view_layer.update()
    
    return ratio

def prepare_low_poly(high_poly, target_triangles):
    """
    Duplicates high poly, decimates it, and unwraps UVs.
    """
    print("Duplicating high poly..."); sys.stdout.flush()
    bpy.ops.object.select_all(action='DESELECT')
    high_poly.select_set(True)
    bpy.context.view_layer.objects.active = high_poly
    bpy.ops.object.duplicate()
    bpy.context.view_layer.update()
    
    low_poly = bpy.context.active_object
    low_poly.name = "LowPoly"
    print("Mesh duplicated. Cleaning low poly for decimation..."); sys.stdout.flush()
    
    # --- Intelligent Attribute Cleanup ---
    print("Intelligently cleaning redundant attributes...")
    all_attrs = list(low_poly.data.attributes)
    def get_base_name(name):
        return name.strip('_').upper()
    attr_groups = {}
    for attr in all_attrs:
        base = get_base_name(attr.name)
        if base not in attr_groups:
            attr_groups[base] = []
        attr_groups[base].append(attr)
    for base, variants in attr_groups.items():
        if base in ['POSITION', 'NORMAL', 'TEXCOORD0']: continue
        if base == 'COLOR0':
            variant_to_keep = next((v for v in variants if v.name == 'COLOR_0'), variants[0])
        else:
            variant_to_keep = sorted(variants, key=lambda v: len(v.name), reverse=True)[0]
        print(f" - Keeping primary {base}: {variant_to_keep.name}")
        for v in variants:
            if v != variant_to_keep:
                print(f" - Removing redundant variant: {v.name}")
                try: low_poly.data.attributes.remove(v)
                except: pass
    
    # Explicitly remove extra color layers
    for col in list(low_poly.data.color_attributes):
        if col.name != 'COLOR_0' and 'SURFACE_PROPS' not in col.name.upper() and 'EDGEGRADIENT' not in col.name.upper():
            print(f" - Removing extra color layer: {col.name}")
            low_poly.data.color_attributes.remove(col)
    
    # Ensure FIX_COLOR_0 is present as FLOAT_VECTOR (Force VEC3 FLOAT export)
    if "FIX_COLOR_0" not in low_poly.data.attributes:
        print(" - Creating FIX_COLOR_0 attribute as FLOAT_VECTOR (3-component FLOAT).")
        low_poly.data.attributes.new(name="FIX_COLOR_0", type='FLOAT_VECTOR', domain='POINT')
        
    # Explicitly remove extra color layers (be very aggressive)
    all_colors = [c.name for c in low_poly.data.color_attributes]
    for name in all_colors:
        if name != 'COLOR_0': # If COLOR_0 exists for some reason, keep it, but we prefer FIX_COLOR_0
             low_poly.data.color_attributes.remove(low_poly.data.color_attributes[name])
    
    # Check regular attributes too for stray colors
    for attr in list(low_poly.data.attributes):
        if attr.name.startswith('COLOR_') and attr.name != 'COLOR_0':
             low_poly.data.attributes.remove(attr)
    
    bpy.context.view_layer.update()
    print("Low poly cleaned."); sys.stdout.flush()
    
    # Decimate
    decimate_mesh(low_poly, target_triangles)
    
    # --- Cleanup and Validate Low Poly ---
    print("Cleaning up LowPoly...")
    bpy.context.view_layer.objects.active = low_poly
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    # Remove degenerate/zero-area elements that can break normal mapping/export
    bpy.ops.mesh.dissolve_degenerate(threshold=0.0001)
    # Ensure no doubled vertices post-decimation (sometimes happens with complex modifiers)
    bpy.ops.mesh.remove_doubles(threshold=0.0001)
    bpy.ops.object.mode_set(mode='OBJECT')
    
    # Clear custom normals (CRITICAL for normal maps on decimated meshes)
    if low_poly.data.has_custom_normals:
        print("Clearing custom split normals from LowPoly...")
        bpy.ops.object.select_all(action='DESELECT')
        low_poly.select_set(True)
        bpy.context.view_layer.objects.active = low_poly
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.customdata_custom_splitnormals_clear()
        bpy.ops.object.mode_set(mode='OBJECT')

    # Apply smooth shading to low poly (CRITICAL for normal maps)
    print("Applying smooth shading to LowPoly...")
    bpy.ops.object.select_all(action='DESELECT')
    low_poly.select_set(True)
    bpy.context.view_layer.objects.active = low_poly
    bpy.ops.object.shade_smooth()
    
    # UV Unwrap
    print("Preparing for UV unwrap..."); sys.stdout.flush()
    bpy.context.view_layer.update()
    
    print("Switching to EDIT mode..."); sys.stdout.flush()
    bpy.ops.object.mode_set(mode='EDIT')
    print("Entered EDIT mode."); sys.stdout.flush()
    
    bpy.ops.mesh.select_all(action='SELECT')
    # Use Smart Project for robust automatic UVs
    print("Running Smart UV Project..."); sys.stdout.flush()
    bpy.ops.uv.smart_project(angle_limit=1.15192, margin_method='SCALED', island_margin=0.01)
    print("UV Project finished."); sys.stdout.flush()
    
    # Deselect all BEFORE switching back to Object mode
    print("Deselecting and returning to OBJECT mode..."); sys.stdout.flush()
    bpy.ops.mesh.select_all(action='DESELECT')
    
    # Return to Object mode to finalize and sanitize
    bpy.ops.object.mode_set(mode='OBJECT')
    print("Returned to OBJECT mode."); sys.stdout.flush()
    
    # --- Final Sanitization for universal compatibility (MS 3D Viewer Fix) ---
    print("Performing final sanitization on LowPoly...")
    standard_names = ['POSITION', 'NORMAL', 'TEXCOORD_0', 'TEXCOORD_1', 'COLOR_0', 'COLOR_1']
    
    # In Blender's Python API, we loop through attributes and remove those that won't map to standard glTF
    for attr in list(low_poly.data.attributes):
        # We also keep our "FIX_COLOR_0" if it hasn't been renamed yet, though we prefer COLOR_0
        if attr.name not in standard_names and attr.name != 'FIX_COLOR_0' and not attr.name.startswith('UVMap'):
            print(f" - Stripping non-standard attribute: {attr.name}")
            try: low_poly.data.attributes.remove(attr)
            except: pass

    # Ensure shading is updated and normals are solid
    print("Recalculating normals..."); sys.stdout.flush()
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    # Refreshes normals at the data level (more robust in background mode than ops.mesh.customdata_normals_clear)
    low_poly.data.update()
    bpy.ops.mesh.normals_make_consistent(inside=False)
    bpy.ops.object.mode_set(mode='OBJECT')
    
    # Final smooth shading reinforcement
    bpy.ops.object.shade_smooth()
    
    bpy.context.view_layer.update()
    print("Returned to OBJECT mode and sanitized."); sys.stdout.flush()
    
    return low_poly

def setup_baking_material(obj, width, height):
    """
    Sets up a material with a texture node for baking.
    """
    print("Setting up baking material..."); sys.stdout.flush()
    mat = bpy.data.materials.new(name="BakeMaterial")
    print("New material created."); sys.stdout.flush()
    try:
        mat.use_nodes = True
        print("use_nodes set to True."); sys.stdout.flush()
    except Exception as e:
        print(f"Error setting use_nodes: {e}")
    obj.data.materials.clear()
    obj.data.materials.append(mat)
    
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    
    # Clear existing nodes
    for node in nodes:
        nodes.remove(node)
        
    # Standard PBR nodes
    node_principled = nodes.new('ShaderNodeBsdfPrincipled')
    node_output = nodes.new('ShaderNodeOutputMaterial')
    links.new(node_principled.outputs['BSDF'], node_output.inputs['Surface'])
    
    # Texture node for baking
    bake_tex = nodes.new('ShaderNodeTexImage')
    bake_tex.name = "BAKE_TARGET"
    bake_tex.location = (-300, 0)
    
    return mat, bake_tex

def pack_orm_textures(ao_path, rough_path, metal_path, output_path):
    """
    Packs AO, Roughness, and Metallic textures into a single ORM map.
    Red: AO (Default 1.0), Green: Roughness, Blue: Metalness
    """
    print(f"Packing ORM texture to {output_path}...")
    
    try:
        import numpy as np
        # We'll use Blender's internal image handling to load and numpy for packing
        
        def load_image_to_numpy(path):
            if not os.path.exists(path):
                return None
            img = bpy.data.images.load(path)
            # pixels is a flat float array (RGBA)
            pixels = np.array(img.pixels[:])
            width, height = img.size
            # Reshape to (H, W, 4)
            pixels = pixels.reshape((height, width, 4))
            bpy.data.images.remove(img)
            return pixels, width, height

        ao_data = load_image_to_numpy(ao_path) if ao_path else None
        rough_data = load_image_to_numpy(rough_path) if rough_path else None
        metal_data = load_image_to_numpy(metal_path) if metal_path else None
        
        # Use dimensions from any available map
        for data in [ao_data, rough_data, metal_data]:
            if data:
                pixels, w, h = data
                break
        else:
            print("No ORM components found to pack.")
            return False
            
        # Create ORM array (H, W, 4) - RGB + Alpha
        orm_pixels = np.zeros((h, w, 4), dtype=np.float32)
        orm_pixels[:, :, 3] = 1.0 # Alpha
        
        # Red Channel: AO (Default 1.0)
        if ao_data:
            orm_pixels[:, :, 0] = ao_data[0][:, :, 0]
        else:
            orm_pixels[:, :, 0] = 1.0
            
        # Green Channel: Roughness
        if rough_data:
            orm_pixels[:, :, 1] = rough_data[0][:, :, 0] # Assume grayscale, take R channel
        else:
            orm_pixels[:, :, 1] = 0.5 # Fallback
            
        # Blue Channel: Metalness
        if metal_data:
            orm_pixels[:, :, 2] = metal_data[0][:, :, 0] # Assume grayscale, take R channel
        else:
            orm_pixels[:, :, 2] = 0.0 # Fallback
            
        # Save ORM image
        orm_img = bpy.data.images.new("ORM_Pack", width=w, height=h)
        orm_img.pixels = orm_pixels.flatten().tolist()
        orm_img.filepath_raw = output_path
        orm_img.file_format = 'PNG'
        orm_img.save()
        bpy.data.images.remove(orm_img)
        print("ORM packing successful.")
        return True
        
    except Exception as e:
        print(f"Error during ORM packing: {e}")
        import traceback
        traceback.print_exc()
        return False

def bake_and_save(high_poly, low_poly, bake_type, image_name, res_w, res_h, output_path):
    """
    Bakes a specific pass and saves it.
    """
    print(f"Baking {bake_type}...")
    
    # Create image
    img = bpy.data.images.new(image_name, width=res_w, height=res_h)
    
    # Ensure low poly has the bake material and texture node active
    mat = low_poly.data.materials[0]
    bake_node = mat.node_tree.nodes["BAKE_TARGET"]
    bake_node.image = img
    mat.node_tree.nodes.active = bake_node
    
    # Selection setup: High selected, Low active
    bpy.ops.object.select_all(action='DESELECT')
    high_poly.select_set(True)
    low_poly.select_set(True)
    bpy.context.view_layer.objects.active = low_poly
    
    # Bake settings
    bpy.context.scene.render.engine = 'CYCLES'
    # Use CPU by default if GPU fails or not available, but try to use GPU
    try:
        bpy.context.scene.cycles.device = 'GPU'
    except:
        bpy.context.scene.cycles.device = 'CPU'
        
    bpy.context.scene.render.bake.use_selected_to_active = True
    bpy.context.scene.render.bake.margin = 16
    bpy.context.scene.render.bake.cage_extrusion = 0.3 # Increased for complex minis
    bpy.context.scene.render.bake.use_clear = True
    bpy.context.scene.render.bake.use_clear = True
    
    # Configure pass settings for Diffuse to avoid black shadows
    if bake_type == 'DIFFUSE':
        bpy.context.scene.render.bake.use_pass_direct = False
        bpy.context.scene.render.bake.use_pass_indirect = False
        bpy.context.scene.render.bake.use_pass_color = True
    
    # Perform bake
    print(f"Starting bake operator for {bake_type}...")
    try:
        bpy.ops.object.bake(type=bake_type)
        print(f"Bake operator for {bake_type} finished.")
    except Exception as e:
        print(f"ERROR: Bake operator failed for {bake_type}: {e}")
        return None
    
    # Save image if output_path is provided
    if output_path:
        img.filepath_raw = output_path
        img.file_format = 'PNG'
        img.save()
        print(f"Saved {bake_type} bake to {output_path}")
    
    return img

def bake_to_vertex_colors(high_poly, low_poly, target_attr_name="FIX_COLOR_0"):
    """
    Bakes the vertex colors from high poly to low poly attributes.
    """
    print(f"Baking vertex colors from HighPoly to LowPoly attribute '{target_attr_name}'...")
    
    # 1. Ensure LowPoly has the target attribute and it is active for baking
    if target_attr_name not in low_poly.data.color_attributes:
        low_poly.data.color_attributes.new(name=target_attr_name, type='FLOAT_COLOR', domain='POINT')
        
    # Set it active for baking
    attr = low_poly.data.color_attributes[target_attr_name]
    # In newer Blender versions (4.2+), we use active_render or set the active layer
    try:
        attr.active_render = True
    except:
        pass
    
    # Try to set it as the active color attribute for the mesh data
    try:
        low_poly.data.color_attributes.active_color = attr
    except:
        pass
    
    # 2. Setup high poly material to emit vertex colors
    # We'll use the existing import_model logic which links target_col_attr to Base Color
    # So we just need to link it to Emission for the bake.
    
    # 3. Perform the bake
    bpy.ops.object.select_all(action='DESELECT')
    high_poly.select_set(True)
    low_poly.select_set(True)
    bpy.context.view_layer.objects.active = low_poly
    
    bpy.context.scene.render.engine = 'CYCLES'
    bpy.context.scene.render.bake.use_selected_to_active = True
    bpy.context.scene.render.bake.target = 'VERTEX_COLORS'
    
    try:
        # We use EMIT to transfer vertex colors accurately
        # Set Base Color to white to avoid tinting if using Diffuse, 
        # but EMIT is better.
        print("Starting vertex color transfer bake (EMIT)...")
        # Ensure only the active color attribute is baked
        bpy.ops.object.bake(type='EMIT')
        print("Vertex color transfer bake finished.")
        return True
    except Exception as e:
        print(f"ERROR: Vertex color bake failed: {e}")
        return False
    finally:
        # Reset bake target for future image bakes
        bpy.context.scene.render.bake.target = 'IMAGE_TEXTURES'

def apply_baked_textures(low_poly, diffuse_img=None, normal_img=None, surface_props_img=None):
    """
    Connects the baked textures to the material of the low poly model.
    Unpacks surface_props_img (R: Rough, G: Metal, B: Sheen) into the BSDF.
    """
    if not low_poly.data.materials:
        return
        
    mat = low_poly.data.materials[0]
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    principled = next((n for n in nodes if n.type == 'BSDF_PRINCIPLED'), None)
    
    if not principled:
        return
    
    # 1. Base Color & Vertex Color Preservation
    # --------------------------------------------------------
    # To satisfy strict viewers like MS 3D Viewer, we MUST:
    # A) Use separate texture nodes for different PBR slots.
    # B) Officially "use" the COLOR_0 attribute so the exporter doesn't flag it as unused.
    # 
    # MIX STRATEGY: Attribute COLOR_0 -> Mix (0.0001) -> Base Color.
    # This is the industry-standard way to ensure vertex colors are exported
    # without visually altering the baked diffuse texture.
    
    attr_node = nodes.new('ShaderNodeAttribute')
    attr_node.attribute_name = "COLOR_0"
    attr_node.location = (principled.location.x - 1000, principled.location.y)
    
    mix_node = nodes.new('ShaderNodeMix')
    mix_node.data_type = 'RGBA'
    mix_node.blend_type = 'MIX'
    mix_node.inputs[0].default_value = 0.0001 # Factor
    mix_node.location = (principled.location.x - 300, principled.location.y)
    links.new(attr_node.outputs['Color'], mix_node.inputs[7]) # Input B
    
    if diffuse_img:
        diff_node = nodes.new('ShaderNodeTexImage')
        diff_node.image = diffuse_img
        diff_node.location = (principled.location.x - 600, principled.location.y)
        links.new(diff_node.outputs['Color'], mix_node.inputs[6]) # Input A
    else:
        # If no texture, use a default mid-gray for Input A
        mix_node.inputs[6].default_value = (0.5, 0.5, 0.5, 1.0)

    links.new(mix_node.outputs[2], principled.inputs['Base Color'])

    # Standardize Specular for non-washed-out look
    spec_input = principled.inputs.get('Specular IOR Level') or principled.inputs.get('Specular')
    if spec_input:
        spec_input.default_value = 0.5
    
    # Ensure Emission is inactive but CLEAN
    e_color_input = principled.inputs.get('Emission Color') or principled.inputs.get('Emission')
    if e_color_input:
        e_color_input.default_value = (0, 0, 0, 1)
    e_strength_input = principled.inputs.get('Emission Strength')
    if e_strength_input:
        e_strength_input.default_value = 0.0

    # 2. Normal Map (Restored)
    if normal_img:
        norm_node = nodes.new('ShaderNodeTexImage')
        norm_node.image = normal_img
        norm_node.image.colorspace_settings.name = 'Non-Color'
        norm_map = nodes.new('ShaderNodeNormalMap')
        links.new(norm_node.outputs['Color'], norm_map.inputs['Color'])
        links.new(norm_map.outputs['Normal'], principled.inputs['Normal'])

    # 3. Surface Properties (Restored)
    if surface_props_img:
        props_node = nodes.new('ShaderNodeTexImage')
        props_node.image = surface_props_img
        props_node.image.colorspace_settings.name = 'Non-Color'
        props_node.location = (principled.location.x - 600, principled.location.y - 300)
        
        sep = nodes.new('ShaderNodeSeparateColor')
        links.new(props_node.outputs['Color'], sep.inputs['Color'])
        
        links.new(sep.outputs['Red'], principled.inputs['Roughness'])
        links.new(sep.outputs['Green'], principled.inputs['Metallic'])
        
        sheen_input = principled.inputs.get('Sheen Weight') or principled.inputs.get('Sheen')
        if sheen_input:
            links.new(sep.outputs['Blue'], sheen_input)

def main():
    # Parse arguments after '--'
    if "--" not in sys.argv:
        args_list = []
    else:
        args_list = sys.argv[sys.argv.index("--") + 1:]
        
    parser = argparse.ArgumentParser(description="Blender Model Processor")
    parser.add_argument("--input", required=True, help="Path to high poly model")
    parser.add_argument("--output", required=True, help="Path for output model")
    parser.add_argument("--format", default="obj", choices=["obj", "fbx", "glb"], help="Output format")
    parser.add_argument("--triangles", type=int, default=20000, help="Target triangle count")
    parser.add_argument("--resolution", type=int, default=2048, help="Texture resolution")
    parser.add_argument("--bake_diffuse", action="store_true", default=True, help="Bake diffuse color (Default: True)")
    parser.add_argument("--bake_normal", action="store_true", default=True, help="Bake normal map (Default: True)")
    parser.add_argument("--bake_roughness", action="store_true", default=True, help="Bake roughness map (Default: True)")
    parser.add_argument("--bake_metallic", action="store_true", default=True, help="Bake metallic map (Default: True)")
    
    args = parser.parse_args(args_list)
    
    # 0. Workspace Cleanup (Remove debris from previous versions)
    print("Cleaning up work directory..."); sys.stdout.flush()
    cwd = os.getcwd()
    for f in os.listdir(cwd):
        if ".unpacked.gltf" in f or ".unpacked.bin" in f:
            try:
                os.remove(os.path.join(cwd, f))
                print(f" - Removed legacy file: {f}")
            except: pass
            
    clear_scene()
    
    print(f"Processing: {args.input}"); sys.stdout.flush()
    high_poly, decals, surface_props_attr, target_col_attr = import_model(args.input)
    print("Import model finished."); sys.stdout.flush()
    
    # 1. Prepare Low Poly (Decimate the Welded Core)
    if not high_poly and not decals:
        raise RuntimeError("No mesh data loaded.")
        
    source_obj = high_poly if high_poly else decals[0]
    print(f"Decimating {source_obj.name} to {args.triangles} triangles..."); sys.stdout.flush()
    low_poly = prepare_low_poly(source_obj, args.triangles)
    low_poly.name = "LowPoly_Export"
    
    # 2. Baking Setup
    import tempfile 
    import shutil
    import zipfile
    
    output_dir = os.path.dirname(args.output)
    if not os.path.exists(output_dir):
        os.makedirs(output_dir, exist_ok=True)
        
    # Use temp dir for intermediate textures if GLB
    temp_dir = tempfile.mkdtemp()
    bake_dir = temp_dir if args.format != 'obj' else output_dir
    
    setup_baking_material(low_poly, args.resolution, args.resolution)
    all_hp = ([high_poly] if high_poly else []) + decals
    
    # Determine what to bake
    diff_img = None
    norm_img = None
    surface_props_img = None
    
    # - Bake Color (Albedo) using Emissive pass
    if args.bake_diffuse and target_col_attr:
        diff_path = os.path.join(bake_dir, "diffuse.png")
        diff_img = bake_pure_data_pass(all_hp, low_poly, target_col_attr, args.resolution, args.resolution, diff_path)
        
        # Vertex Color Fallback for compatibility
        if args.format == 'glb':
            bake_to_vertex_colors(source_obj, low_poly, "COLOR_0")
            
    # - Bake Surface Props (Rough/Metal/Sheen) using Emissive pass
    if surface_props_attr and (args.bake_roughness or args.bake_metallic):
        print("Baking Surface Properties...")
        props_path = os.path.join(bake_dir, "surface_props.png")
        surface_props_img = bake_pure_data_pass(all_hp, low_poly, surface_props_attr, args.resolution, args.resolution, props_path)
        
    # - Bake Normals (Standard Cycles Normal Bake)
    if args.bake_normal:
        norm_path = os.path.join(bake_dir, "normal.png")
        norm_img = bake_and_save(source_obj, low_poly, 'NORMAL', "NormalBake", args.resolution, args.resolution, norm_path)

    # 3. Apply Textures and Setup PBR
    apply_baked_textures(low_poly, diff_img, norm_img, surface_props_img)
    
    # Standardize factors for GLB
    if args.format == 'glb':
        mat = low_poly.data.materials[0]
        principled = next((n for n in mat.node_tree.nodes if n.type == 'BSDF_PRINCIPLED'), None)
        if principled:
            principled.inputs['Metallic'].default_value = 1.0
            principled.inputs['Roughness'].default_value = 1.0
            
    # 4. Final Export
    export_path = args.output
    if args.format == 'obj':
        export_path = os.path.join(bake_dir, os.path.basename(args.output))
        
    print(f"Exporting as {args.format}..."); sys.stdout.flush()
    bpy.ops.object.select_all(action='DESELECT')
    low_poly.select_set(True)
    bpy.context.view_layer.objects.active = low_poly
    
    if args.format == 'obj':
        bpy.ops.wm.obj_export(filepath=export_path, export_selected_objects=True)
    elif args.format == 'fbx':
        bpy.ops.export_scene.fbx(filepath=export_path, use_selection=True)
    elif args.format == 'glb':
        bpy.ops.export_scene.gltf(
            filepath=export_path, 
            export_format='GLB', 
            use_selection=True,
            export_attributes=True,
            export_extras=True,
            export_tangents=True, # Added to fix normal map shading (MESH_PRIMITIVE_GENERATED_TANGENT_SPACE)
            export_draco_mesh_compression_enable=False, # Universal compatibility
            export_image_format='AUTO'
        )
        # Compatibility Post-Process
        post_process_glb_file(export_path)
        
    # Zip OBJ if needed
    if args.format == 'obj':
        zip_path = args.output if args.output.lower().endswith('.zip') else args.output + ".zip"
        files_to_zip = [os.path.join(bake_dir, f) for f in os.listdir(bake_dir) if f.lower().endswith(('.obj', '.mtl', '.png'))]
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for f in files_to_zip:
                zipf.write(f, os.path.basename(f))
        print(f"ZIP package created: {zip_path}")

    # Cleanup
    shutil.rmtree(temp_dir)
    print("Processing complete!"); sys.stdout.flush()

def post_process_glb_file(export_path):
    """
    Standardizes GLB precision and PBR factors.
    Forces COLOR_0 to VEC3 FLOAT to prevent banding and fix app compatibility.
    """
    print(f"Post-processing {export_path} for final precision fix...")
    import struct
    import json
    try:
        with open(export_path, 'rb') as f:
            header = f.read(12)
            json_h = f.read(8)
            j_len, j_type = struct.unpack('<II', json_h)
            json_data = json.loads(f.read(j_len).decode('utf-8'))
            bin_h = f.read(8)
            b_len, b_type = struct.unpack('<II', bin_h)
            bin_data = bytearray(f.read(b_len))

        modified = False
        # Fix Metallic/Roughness factors to 1.0 (Texture primacy)
        for mat in json_data.get('materials', []):
            pbr = mat.get('pbrMetallicRoughness', {})
            if pbr.get('metallicFactor') != 1.0 or pbr.get('roughnessFactor') != 1.0:
                pbr['metallicFactor'] = 1.0
                pbr['roughnessFactor'] = 1.0
                mat['pbrMetallicRoughness'] = pbr
                modified = True

        # Recode Vertex Colors to FLOAT precision (VEC3 FLOAT)
        new_bin_data = bin_data
        for mesh in json_data.get('meshes', []):
            for prim in mesh.get('primitives', []):
                attrs = prim.get('attributes', {})
                
                # Check for COLOR_0 optimization
                target = 'COLOR_0' if 'COLOR_0' in attrs else None
                if target:
                    acc = json_data['accessors'][attrs[target]]
                    if acc.get('componentType') != 5126 or acc.get('type') != 'VEC3':
                        bv = json_data['bufferViews'][acc['bufferView']]
                        start = bv.get('byteOffset', 0) + acc.get('byteOffset', 0)
                        count = acc['count']
                        new_floats = []
                        stride = bv.get('byteStride', 4) if acc.get('type') == 'VEC4' else 12
                        for i in range(count):
                            idx = start + i * stride
                            if acc.get('type') == 'VEC4' and acc.get('componentType') == 5121:
                                r, g, b, a = bin_data[idx:idx+4]
                                new_floats.extend([r/255.0, g/255.0, b/255.0])
                            elif acc.get('type') == 'VEC3' and acc.get('componentType') == 5126:
                                new_floats.extend(struct.unpack('<3f', bin_data[idx:idx+12]))
                        
                        new_offset = (len(new_bin_data) + 3) & ~3
                        new_bin_data.extend(b'\x00' * (new_offset - len(new_bin_data)))
                        float_bytes = struct.pack(f'<{len(new_floats)}f', *new_floats)
                        new_bin_data.extend(float_bytes)
                        acc.update({'componentType': 5126, 'type': 'VEC3', 'byteOffset': 0, 'normalized': False})
                        # Explicitly add target: 34962 (ARRAY_BUFFER) to satisfy glTF-Validator
                        json_data['bufferViews'].append({'buffer': 0, 'byteOffset': new_offset, 'byteLength': len(float_bytes), 'target': 34962})
                        acc['bufferView'] = len(json_data['bufferViews']) - 1
                        modified = True

                # --- Final Sanitization for MS 3D Viewer ---
                # Remove all custom attributes (those starting with an underscore)
                for key in list(attrs.keys()):
                    if key.startswith('_'):
                        print(f" - Stripping custom attribute {key} from final GLB JSON.")
                        del attrs[key]
                        modified = True

        if modified:
            # Padding binary data to 4-byte boundaries (required by glTF spec)
            while len(new_bin_data) % 4 != 0: new_bin_data.append(0)

            # IMPORTANT: Re-map buffer length to match the final padded binary payload
            if 'buffers' in json_data and len(json_data['buffers']) > 0:
                json_data['buffers'][0]['byteLength'] = len(new_bin_data)

            json_bytes = json.dumps(json_data, separators=(',', ':')).encode('utf-8')
            while len(json_bytes) % 4 != 0: json_bytes += b' '
            
            # Recalculate total length for GLB header
            total_len = 12 + 8 + len(json_bytes) + 8 + len(new_bin_data)
            with open(export_path, 'wb') as f:
                f.write(struct.pack('<4sII', b'glTF', 2, total_len))
                f.write(struct.pack('<II', len(json_bytes), 0x4E4F534A))
                f.write(json_bytes)
                f.write(struct.pack('<II', len(new_bin_data), 0x004E4942))
                f.write(new_bin_data)
            print("Post-processing (VEC3 FLOAT + Sanitization) applied successfully.")
    except Exception as e:
        print(f"WARNING: Post-processing failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
