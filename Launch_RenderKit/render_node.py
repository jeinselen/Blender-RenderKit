import bpy
from .render_variables import replaceVariables

class RENDERKIT_OT_render_node(bpy.types.Operator):
	bl_idname = "node.render_node"
	bl_label = "Render Node"
	bl_options = {'REGISTER', 'UNDO'}
	
	def execute(self, context):
		prefs = context.preferences.addons[__package__].preferences
		settings = context.scene.render_kit_settings
		
		# Check for active mesh object
		obj = context.active_object
		if not obj or obj.type != 'MESH':
			self.report({'ERROR'}, "No active mesh object selected")
			return {'CANCELLED'}
		
		# Check for active node
		node = context.active_node
		if not node:
			self.report({'ERROR'}, "No active node selected")
			return {'CANCELLED'}
		
		# Get active scene
		scene = context.scene
		
		# Store original settings
		original_device = scene.cycles.device
		original_samples = scene.cycles.samples
		original_bake = scene.cycles.bake_type
		original_clear = scene.render.bake.use_clear
		original_selectedtoactive = scene.render.bake.use_selected_to_active
		original_splitmaterials = scene.render.bake.use_split_materials
		
		# Ensure the selected UV map exists
		uvmap = obj.data.uv_layers.get(settings.node_uvmap)
		if not uvmap:
			self.report({'ERROR'}, f"UV map '{settings.node_uvmap}' not found")
			return {'CANCELLED'}
		obj.data.uv_layers.active = uvmap
		
		# Create a new image to bake to
		image = bpy.data.images.new("RenderKit_RenderNodeImage", width=settings.node_resolution_x, height=settings.node_resolution_y, alpha=True, float_buffer=False, stereo3d=False, is_data=False, tiled=False)
		
		# Add an image texture node to the material for baking
		material = obj.active_material
		node_tree = material.node_tree
		image_node = node_tree.nodes.new(type='ShaderNodeTexImage')
		image_node.image = image
		image_node.select = True
		node_tree.nodes.active = image_node
		
		# Connect the selected node output to the image texture node
		links = node_tree.links
		selected_output = settings.node_output if settings.node_output in node.outputs else node.outputs[0].name
		links.new(node.outputs[selected_output], image_node.inputs['Color'])
		
		# Set bake settings
		scene.cycles.device = 'GPU' if settings.node_render_device == 'GPU' else 'CPU'
		scene.cycles.samples = settings.node_samples
		scene.cycles.bake_type = 'EMIT'
		scene.render.bake.use_clear = True
		scene.render.bake.use_selected_to_active = False
		scene.render.bake.use_split_materials = False
		
		# Bake the procedural texture to the image
		bpy.ops.object.bake(type='EMIT')
		
		# Save the baked image
		abs_path = bpy.path.abspath(replaceVariables(settings.node_filepath))
		if not os.path.exists(abs_path):
			os.makedirs(abs_path)
		image.filepath_raw = abs_path
		image.file_format = 'PNG'
		image.save()
		
		# Remove the temporary image texture node
		node_tree.nodes.remove(image_node)
		
		# Remove the temporary image data
		bpy.data.images.remove(bpy.data.images['RenderKit_RenderNodeImage'], do_unlink=True)
		
		# Restore original settings
		scene.cycles.device = original_device
		scene.cycles.samples = original_samples
		scene.cycles.bake_type = original_bake
		scene.render.bake.use_clear = original_clear
		scene.render.bake.use_selected_to_active = original_selectedtoactive
		scene.render.bake.use_split_materials = original_splitmaterials
		
		self.report({'INFO'}, f"Node render saved to {abs_path}")
		return {'FINISHED'}



class RENDERKIT_PT_render_node(bpy.types.Panel):
	bl_space_type = 'NODE_EDITOR'
	bl_region_type = 'UI'
	bl_category = 'Node'
	bl_label = "Render Node to Image"
	
	@classmethod
	def poll(cls, context):
		obj = context.active_object
		return (context.space_data.tree_type == 'ShaderNodeTree' and context.object.active_material is not None and obj and obj.type == 'MESH' and context.active_node)
	
	def draw(self, context):
		prefs = context.preferences.addons[__package__].preferences
		settings = context.scene.render_kit_settings
		
		layout = self.layout
		
		layout.prop_search(settings, "node_uvmap", obj.data, "uv_layers")
		layout.prop(settings, "node_output")
		layout.prop(settings, "node_render_device")
		layout.prop(settings, "node_resolution_x")
		layout.prop(settings, "node_resolution_y")
		layout.prop(settings, "node_samples")
		layout.prop(settings, "node_color_space")
		layout.prop(settings, "node_filepath")
		
		layout.operator(RENDERKIT_OT_render_node.bl_idname)



# Class Registration

def menu_func(self, context):
	self.layout.operator(RENDERKIT_OT_render_node.bl_idname)

def register():
	bpy.utils.register_class(RENDERKIT_OT_render_node)
	bpy.utils.register_class(RENDERKIT_PT_render_node)
	bpy.types.NODE_MT_context_menu.append(menu_func)

def unregister():
	bpy.utils.unregister_class(RENDERKIT_OT_render_node)
	bpy.utils.unregister_class(RENDERKIT_PT_render_node)
	bpy.types.NODE_MT_context_menu.remove(menu_func)

if __name__ == "__main__":
	register()
