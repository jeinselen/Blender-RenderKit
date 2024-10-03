import bpy
import os
import time
from .render_variables import replaceVariables, OutputVariablePopup
from .utility_time import secondsToStrings, secondsToReadable, readableToSeconds
from .utility_filecheck import checkExistingAndIncrement



class RENDERKIT_OT_render_node(bpy.types.Operator):
	bl_idname = "node.render_node"
	bl_label = "Render Node"
	bl_options = {'REGISTER', 'UNDO'}
	
	def invoke(self, context, event):
			return context.window_manager.invoke_props_dialog(self)
	
	def draw(self, context):
		try:
			layout = self.layout
			layout.label(text="Blender will be unresponsive while rendering")
		except Exception as exc:
			print(str(exc) + ' | Error in Render Kit — Render Node confirmation header')
	
	def execute(self, context):
		prefs = context.preferences.addons[__package__].preferences
		settings = context.scene.render_kit_settings
		
		# Check for active mesh object
		obj = context.active_object
		if not obj or obj.type != 'MESH':
			self.report({'ERROR'}, "Render Kit — Render Node no active mesh object selected")
			return {'CANCELLED'}
		
		# Ensure the active object is selected in layout
		obj.select_set(True)
		
		# Check for active node
		source_node = context.active_node
		if not source_node:
			self.report({'ERROR'}, "Render Kit — Render Node no active node selected")
			return {'CANCELLED'}
		
		# Check if the selected output exists
		if settings.node_output not in [output.name for output in source_node.outputs]:
			self.report({'ERROR'}, f"Render Kit — Render Node output '{settings.node_output}' not found")
			return {'CANCELLED'}
		
		# Check for selected UV map
		uvmap = obj.data.uv_layers.get(settings.node_uvmap)
		if not uvmap:
			self.report({'ERROR'}, f"Render Kit — Render Node UV map '{settings.node_uvmap}' not found")
			return {'CANCELLED'}
		
		# Push undo state (because attempting to manually restore everything was way too frustrating)
		bpy.ops.ed.undo_push()
		
		# Get active scene
		scene = context.scene
		
		# Get the file path and do the initial variable replacement
		file_path = settings.node_filepath + '.' + settings.node_format.replace("OPEN_EXR", "EXR").lower()
		file_path = replaceVariables(file_path) # Must be completed before the active nodes change
		
		# Get the output socket by name
		original_node_output = {output.name: output for output in source_node.outputs}
		output_socket = original_node_output[settings.node_output]
		
		# Set active output type
		output_type = 'COMBINED' if output_socket.type == 'SHADER' else ('NORMAL' if output_socket.type == 'VECTOR' and output_socket.name == 'Normal' else 'EMIT')
		
		# Set active UV map
		obj.data.uv_layers.active = uvmap
		
		# Create render image
		bpy.ops.image.new(
			name='RenderKit_RenderNodeImage',
			width=settings.node_resolution_x,
			height=settings.node_resolution_y,
			color=(0.0, 0.0, 0.0, 0.0),
			alpha=True,
			generated_type='BLANK',
			float=True,
			use_stereo_3d=False,
			tiled=False)
		image = bpy.data.images["RenderKit_RenderNodeImage"]
		if settings.node_format != 'OPEN_EXR':
			image.use_half_precision = True
		
		# Get active node tree
		node_tree = obj.active_material.node_tree
		
		# Create temporary output, and image nodes for rendering
		output_node = node_tree.nodes.new(type='ShaderNodeOutputMaterial')
		output_node.select = True
		node_tree.nodes.active = output_node
		
		image_node = node_tree.nodes.new(type='ShaderNodeTexImage')
		image_node.image = image
		image_node.select = True
		node_tree.nodes.active = image_node
		
		emission_node = False
		diffuse_node = False
		
		# Connect source node output socket to output node input socket
		if output_socket:
			# For further development of custom types, use these lines in the Blender Console:
			# C.active_object.active_material.node_tree.nodes.active.outputs[0].name
			# C.active_object.active_material.node_tree.nodes.active.outputs[0].type
			if output_type == 'EMIT':
				emission_node = node_tree.nodes.new(type='ShaderNodeEmission')
				node_tree.links.new(output_socket, emission_node.inputs[0])
				node_tree.links.new(emission_node.outputs[0], output_node.inputs[0])
			if output_type == 'NORMAL':
				diffuse_node = node_tree.nodes.new(type='ShaderNodeBsdfDiffuse')
				node_tree.links.new(output_socket, diffuse_node.inputs[2])
				node_tree.links.new(diffuse_node.outputs[0], output_node.inputs[0])
			else:
				node_tree.links.new(output_socket, output_node.inputs[0])
		else:
			self.report({'ERROR'}, "Render Kit — Render Node could not find the selected output socket")
			return {'CANCELLED'}
		
		# Store original settings
		# TODO: remove this block if we undo to remove elements
		original_engine = scene.render.engine
		original_film = scene.render.film_transparent
		original_device = scene.cycles.device
		original_samples = scene.cycles.samples
		original_bake = scene.cycles.bake_type
		original_space = scene.render.bake.normal_space
		original_view = scene.render.bake.view_from
		original_target = scene.render.bake.target
		original_margin = scene.render.bake.margin
		original_clear = scene.render.bake.use_clear
		original_selectedtoactive = scene.render.bake.use_selected_to_active
		original_splitmaterials = scene.render.bake.use_split_materials
		
		# Set bake settings
		scene.render.engine = 'CYCLES'
		scene.render.film_transparent = True
		scene.cycles.device = 'GPU' if settings.node_render_device == 'GPU' else 'CPU'
		scene.cycles.samples = settings.node_samples
		scene.cycles.bake_type = output_type
		scene.render.bake.normal_space = 'TANGENT'
		scene.render.bake.view_from = 'ABOVE_SURFACE'
		scene.render.bake.target = 'IMAGE_TEXTURES'
		scene.render.bake.margin = settings.node_margin
		scene.render.bake.use_clear = False
		scene.render.bake.use_selected_to_active = False
		scene.render.bake.use_split_materials = False
		
		# Start render time
		settings.start_date = str(time.time())
		
		# Render to image
		bpy.ops.object.bake(type=output_type)
		
		# Calculate render time and check for serial number
		render_time = round(time.time() - float(settings.start_date), 2)
		
		# Check for serial number usage in the file path
		settings.output_file_serial_used = True if '{serial}' in file_path else False
		
		# Replace variables (part two, this time with all of the custom elements)
		file_path = replaceVariables(file_path, rendertime=render_time, serial=settings.output_file_serial, socket=settings.node_output)
		
		# Increment the output serial number if it was used in the output path
		if settings.output_file_serial_used:
			settings.output_file_serial += 1
			settings.output_file_serial_used = False
		
		# Check for existing directory and files
		file_path = checkExistingAndIncrement(file_path, overwrite=settings.node_overwrite)
		
		# Save texture file
		image.filepath_raw = file_path
		image.file_format = settings.node_format
		image.save()
		
		# Remove temporary nodes and image data
		# TODO: remove this block if we undo to remove elements
		if emission_node: node_tree.nodes.remove(emission_node)
		if diffuse_node: node_tree.nodes.remove(diffuse_node)
		node_tree.nodes.remove(output_node)
		node_tree.nodes.remove(image_node)
		bpy.data.images.remove(image)
		
		# Restore original settings
		# TODO: remove this block if we undo to remove elements
		scene.render.engine = original_engine
		scene.render.film_transparent = original_film
		scene.cycles.device = original_device
		scene.cycles.samples = original_samples
		scene.cycles.bake_type = original_bake
		scene.render.bake.normal_space = original_space
		scene.render.bake.view_from = original_view
		scene.render.bake.target = original_target
		scene.render.bake.margin = original_margin
		scene.render.bake.use_clear = original_clear
		scene.render.bake.use_selected_to_active = original_selectedtoactive
		scene.render.bake.use_split_materials = original_splitmaterials
		
		# Reselect original active node
		source_node.select = True
		node_tree.nodes.active = source_node
		
		# Provide success feedback
		self.report({'INFO'}, f"Node render saved to {file_path}")
		if prefs.rendernode_confirm:
			self.show_completion_popup(context, file_path, render_time)
		
		return {'FINISHED'}
	
	def show_completion_popup(self, context, filepath, rendertime):
		def draw(self, context):
			self.layout.label(text=filepath)
		context.window_manager.popup_menu(draw, title="Render Node Completed " + secondsToReadable(rendertime), icon='NODE_TEXTURE') # NODE NODE_SEL NODETREE NODE_TEXTURE SHADING_RENDERED SHADING_TEXTURE



###########################################################################
# UI rendering classes

class RENDERKIT_PT_render_node(bpy.types.Panel):
	bl_space_type = 'NODE_EDITOR'
	bl_region_type = 'UI'
	bl_category = 'Node'
	bl_label = "Render Node to Image"
	
	@classmethod
	def poll(cls, context):
		prefs = context.preferences.addons[__package__].preferences
		obj = context.active_object
		return (prefs.rendernode_enable and context.space_data.tree_type == 'ShaderNodeTree' and context.object.active_material is not None and obj and obj.type == 'MESH' and context.active_node)
		# context.scene.node_tree.type ?
	
	def draw(self, context):
		settings = context.scene.render_kit_settings
		layout = self.layout
		
		# Primary items that might actually need to change
		layout.prop_search(settings, "node_uvmap", context.active_object.data, "uv_layers")
		layout.prop_search(settings, "node_output", context.active_node, "outputs")
		
		# Render node button
		button = layout.row()
#		no_output = False if settings.node_output in [output.name for output in source_node.outputs] else True
#		no_uvmap = False if context.active_object.data.uv_layers.get(settings.node_uvmap) else True
#		if no_output or no_uvmap:
		if settings.node_output not in [output.name for output in context.active_node.outputs] or not context.active_object.data.uv_layers.get(settings.node_uvmap):
			button.active = False
			button.enabled = False
		button.operator(RENDERKIT_OT_render_node.bl_idname)

class RENDERKIT_PT_render_node_settings(bpy.types.Panel):
	bl_space_type = "NODE_EDITOR"
	bl_region_type = "UI"
	bl_category = 'Node'
	bl_parent_id = "RENDERKIT_PT_render_node"
	bl_options = {'DEFAULT_CLOSED'}
	bl_label = "Settings"
	
	@classmethod
	def poll(cls, context):
		return True
	
	def draw_header(self, context):
		try:
			layout = self.layout
		except Exception as exc:
			print(str(exc) + " | Error in Render Kit — Render Node Settings panel header")
			
	def draw(self, context):
		try:
			settings = context.scene.render_kit_settings
			layout = self.layout
			
			# Naming variables popup and output serial number
			ops = layout.operator(OutputVariablePopup.bl_idname, text="Variable List", icon="LINENUMBERS_OFF")
			ops.postrender = True
			ops.noderender = True
			ops.autoclose = True
			input = layout.row()
			if not '{serial}' in settings.node_filepath:
				input.active = False
				input.enabled = False
			input.prop(settings, 'output_file_serial')
			
			# Output filepath
			layout.prop(settings, "node_filepath")
			layout.prop(settings, "node_overwrite")
			
			# Output format
			layout.prop(settings, "node_format", expand=True)
			
			# Render device
			layout.prop(settings, "node_render_device", expand=True)
			
			# Render settings
			grid = layout.grid_flow(row_major=True, columns=2, even_columns=True, even_rows=True, align=True)
			grid.prop(settings, "node_resolution_x", text='X')
			grid.prop(settings, "node_resolution_y", text='Y')
			grid.prop(settings, "node_samples")
			grid.prop(settings, "node_margin")
			
			# Mesh format
#			layout.prop(settings, "node_evaluated")
			
		except Exception as exc:
			print(str(exc) + " | Error in Render Kit — Render Node Settings panel")



# Class Registration

#def menu_func(self, context):
#	self.layout.operator(RENDERKIT_OT_render_node.bl_idname)

classes = (RENDERKIT_OT_render_node, RENDERKIT_PT_render_node, RENDERKIT_PT_render_node_settings,)

def register():
	# Register classes
	for cls in classes:
		bpy.utils.register_class(cls)
#	bpy.types.NODE_MT_context_menu.append(menu_func)

def unregister():
	# Deregister classes
	for cls in reversed(classes):
		bpy.utils.unregister_class(cls)
#	bpy.types.NODE_MT_context_menu.remove(menu_func)

if __name__ == "__main__":
	register()
	