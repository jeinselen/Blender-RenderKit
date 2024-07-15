import bpy
import os
from re import search



###########################################################################
# Batch Render Functions
# •Process batch rendering queue
#	•Cameras
#	•Collections
#	•Items (objects and/or lights)
#	•Images (requires specific folder input and target material node)
# •Set target material > node for Batch Render Images

# Process batch rendering queue
class batch_render_start(bpy.types.Operator):
	bl_idname = 'render.batch_render_start'
	bl_label = 'Begin Batch Render'
	bl_description = "Batch render specified elements"
	bl_space_type = "VIEW_3D"
	
	@classmethod
	def poll(cls, context):
		return ( True )
	
	def invoke(self, context, event):
			return context.window_manager.invoke_props_dialog(self)
	
	def draw(self, context):
		try:
			layout = self.layout
			layout.label(text="Blender will be unresponsive while processing, proceed?")
		except Exception as exc:
			print(str(exc) + ' | Error in Render Kit: Begin Batch Render confirmation header')
	
	def execute(self, context):
		settings = context.scene.render_kit_settings
		
		settings.batch_active = True
		
		# Preserve manually entered batch index and values
		original_batch_index = settings.batch_index
		original_batch_factor = settings.batch_factor
		original_batch_random = settings.batch_random
		
		# Batch render cameras
		if settings.batch_type == 'cams':
			# Preserve original active camera and render resolution
			original_camera = context.scene.camera
			original_resolution_x = context.scene.render.resolution_x
			original_resolution_y = context.scene.render.resolution_y
			
			# If cameras are selected
			if len(context.selected_objects) > 0 and len([obj for obj in context.selected_objects if obj.type == 'CAMERA']) > 0:
				source_cameras = [obj for obj in context.selected_objects if obj.type == 'CAMERA']
			
			# If no cameras are selected, check for an active collection with cameras
			elif context.view_layer.active_layer_collection and len(context.view_layer.active_layer_collection.collection.all_objects) > 0 and len([obj for obj in context.view_layer.active_layer_collection.collection.all_objects if obj.type == 'CAMERA']) > 0:
				source_cameras = [obj for obj in context.view_layer.active_layer_collection.collection.all_objects if obj.type == 'CAMERA']
			
			# If still no cameras are available, return cancelled
			else:
				settings.batch_active = False
				print('Render Kit Batch: Cameras not found.')
				return {'CANCELLED'}
			
			# Reset batch index value
			settings.batch_index = 0
			
			# Set length of batch collection
			batch_length = len(source_cameras) - 1
			
			# Render each camera in the list
			for cam in source_cameras:
				# Set batch values
				settings.batch_factor = settings.batch_index / batch_length
				settings.batch_random = hash(settings.batch_factor * 0.9998 + 0.0001) / 1000000 % 1
				
				# Set rendering camera to current camera
				context.scene.camera = cam
				
				# Set scene resolution from camera name if appended "#x#" pattern is found
				resolution_match = search(r'(\d+)x(\d+)$', context.scene.camera.name)
				if resolution_match != None:
					context.scene.render.resolution_x = int(resolution_match.group(1))
					context.scene.render.resolution_y = int(resolution_match.group(2))
					original_camera_name = context.scene.camera.name
					context.scene.camera.name = original_camera_name.replace(resolution_match.group(0), "")
				# If no resolution is supplied, reset to original settings (allows mixing of custom and default resolutions in a single batch render)
				else:
					context.scene.render.resolution_x = original_resolution_x
					context.scene.render.resolution_y = original_resolution_y
				
				# Render
				if settings.batch_range == 'img':
					# Render Still
					bpy.ops.render.render(animation=False, write_still=True, use_viewport=True)
				else:
					# Sequence
					bpy.ops.render.render(animation=True, use_viewport=True)
				
				# Restore camera name if it was changed to remove the resolution
				if resolution_match != None:
					context.scene.camera.name = original_camera_name
				
				# Increment index value
				settings.batch_index += 1
				
			# Restore original active camera and render resolution
			context.scene.camera = original_camera
			context.scene.render.resolution_x = original_resolution_x
			context.scene.render.resolution_y = original_resolution_y
		
		# Batch render collections
		if settings.batch_type == 'cols':
			# If we need to support direct selection of multiple collections...
			# https://blender.stackexchange.com/questions/249139/selecting-a-collection-via-python
			# ...but for now I'm keeping this simpler
			
			# If child collections exist
			if len(context.view_layer.active_layer_collection.children) > 0:
				source_collections = [col for col in context.view_layer.active_layer_collection.children]
			
			# If no collections are available, return cancelled
			else:
				settings.batch_active = False
				print('Render Kit Batch: Collections not found.')
				return {'CANCELLED'}
			
			# Store the render status of each collection and disable
			source_collections_hidden = []
			source_collections_excluded = []
			for col in source_collections:
				# Using both exclude and hide_render status to ensure each collection is for-sure enabled when rendering
				source_collections_hidden.append(col.collection.hide_render)
				source_collections_excluded.append(col.exclude)
				col.collection.hide_render = True
				col.exclude = True
				
			print('hidden status:')
			print(dir(source_collections_hidden))
			print('excluded status:')
			print(dir(source_collections_excluded))
			
			# Reset batch index value
			settings.batch_index = 0
			
			# Set length of batch collection
			batch_length = len(source_collections) - 1
			
			# Render each collection in the list
			for col in source_collections:
				# Set batch values
				settings.batch_factor = settings.batch_index / batch_length
				settings.batch_random = hash(settings.batch_factor * 0.9998 + 0.0001) / 1000000 % 1
				
				# Set current collection name
				settings.batch_collection_name = col.name
				
				# Set current collection rendering status
				col.collection.hide_render = False
				col.exclude = False
				
				# Render
				if settings.batch_range == 'img':
					# Render Still
					bpy.ops.render.render(animation=False, write_still=True, use_viewport=True)
				else:
					# Sequence
					bpy.ops.render.render(animation=True, use_viewport=True)
					
				# Disable the collection again
				col.collection.hide_render = True
				col.exclude = True
				
				# Increment index value
				settings.batch_index += 1
				
			# Restore enabled status
			if len(source_collections_hidden) > 0 and len(source_collections_hidden) == len(source_collections_excluded):
				for i, col in enumerate(source_collections):
					col.collection.hide_render = source_collections_hidden[i]
					col.exclude = source_collections_excluded[i]
			
			# Reset batch rendering variable
			settings.batch_collection_name = ''
		
		# Batch render items
		if settings.batch_type == 'itms':
			# Preserve original item selection
			original_selection = [obj for obj in context.selected_objects]
			
			# Preserve active item
			original_active = context.view_layer.objects.active
			
			# If non-camera items are selected
			if len(context.selected_objects) > 0 and len([obj for obj in context.selected_objects if obj.type != 'CAMERA']) > 0:
				source_items = [obj for obj in context.selected_objects if obj.type != 'CAMERA']
			
			# If no items are selected, check for an active collection with non-camera items
			elif context.view_layer.active_layer_collection and len(context.view_layer.active_layer_collection.collection.all_objects) > 0 and len([obj for obj in context.view_layer.active_layer_collection.collection.all_objects if obj.type != 'CAMERA']) > 0:
				source_items = [obj for obj in context.view_layer.active_layer_collection.collection.all_objects if obj.type != 'CAMERA']
			
			# If still no items are available, return cancelled
			else:
				settings.batch_active = False
				print('Render Kit Batch: Items not found.')
				return {'CANCELLED'}
			
			# Store the render status of each object and disable rendering
			source_items_hidden = []
			for obj in source_items:
				source_items_hidden.append(obj.hide_render)
				obj.hide_render = True
				obj.select_set(False)
			
			# Reset batch index value
			settings.batch_index = 0
			
			# Set length of batch collection
			batch_length = len(source_items) - 1
			
			# Render each item in the list
			for obj in source_items:
				# Set batch values
				settings.batch_factor = settings.batch_index / batch_length
				settings.batch_random = hash(settings.batch_factor * 0.9998 + 0.0001) / 1000000 % 1
				
				# Set current object to selected, active, and renderable
				obj.select_set(True)
				context.view_layer.objects.active = obj
				obj.hide_render = False
				
				# Render
				if settings.batch_range == 'img':
					# Render Still
					bpy.ops.render.render(animation=False, write_still=True, use_viewport=True)
				else:
					# Sequence
					bpy.ops.render.render(animation=True, use_viewport=True)
				
				# Disable the object again (don't worry about active, next loop will reset it)
				obj.select_set(False)
				obj.hide_render = True
				
				# Increment index value
				settings.batch_index += 1
			
			# Restore render status
			if len(source_items_hidden) > 0:
				for i, obj in enumerate(source_items):
					obj.hide_render = source_items_hidden[i]
			
			# Restore original selection
			if original_selection:
				for obj in original_selection:
					obj.select_set(True)
			
			# Restore original active item
			if original_active:
				context.view_layer.objects.active = original_active
		
		# Batch render images
		if settings.batch_type == 'imgs':
			# Get source folder and target names
			source_folder = bpy.path.abspath(settings.batch_images_location)
			source_images = []
			if os.path.isdir(source_folder):
				# Image extensions attribute is undocumented
				# https://blenderartists.org/t/bpy-ops-image-open-supported-formats/1237197/6
				source_images = [f for f in os.listdir(source_folder) if f.lower().endswith(tuple(bpy.path.extensions_image))]
				source_images.sort()
			else:
				settings.batch_active = False
				print('Render Kit Batch: Image source directory not found.')
				return {'CANCELLED'}
				# The folder should be checked in the UI before starting, but this is a backup safety if triggered via Python
			
			# Get target
			target_material = settings.batch_images_material
			target_node = settings.batch_images_node
			target = None
			if bpy.data.materials.get(target_material) and bpy.data.materials[target_material].node_tree.nodes.get(target_node) and bpy.data.materials[target_material].node_tree.nodes.get(target_node).type == 'TEX_IMAGE':
				target = bpy.data.materials[target_material].node_tree.nodes.get(target_node)
			else:
				settings.batch_active = False
				print('Render Kit Batch: Target material node not found.')
				return {'CANCELLED'}
			
			# Save current image, if assigned
			original_image = None
			if target.image.has_data:
				original_image = bpy.data.materials[target_material].node_tree.nodes.get(target_node).image
			
			# Reset batch index value
			settings.batch_index = 0
			
			# Set length of batch collection
			batch_length = len(source_images) - 1
			
			# Batch render images (assumes we've already cancelled if there's an error with the folder)
			for img_file in source_images:
				# Set batch values
				settings.batch_factor = settings.batch_index / batch_length
				settings.batch_random = hash(settings.batch_factor * 0.9998 + 0.0001) / 1000000 % 1
				
				# Import as new image if it doesn't already exist
				image = bpy.data.images.load(os.path.join(source_folder, img_file), check_existing=True)
				
				# Set node image to the new image
				target.image = image
				
				# Render
				if settings.batch_range == 'img':
					# Render Still
					bpy.ops.render.render(animation=False, write_still=True, use_viewport=True)
				else:
					# Sequence
					bpy.ops.render.render(animation=True, use_viewport=True)
				
				# Increment index value
				settings.batch_index += 1
			
			# Reset node to original texture, if previously assigned
			if original_image:
				target.image = original_image
		
		# Restore manually entered batch index
		settings.batch_index = original_batch_index
		
		settings.batch_active = False
		return {'FINISHED'}

# Set target material > node for Batch Render Images
class batch_image_target(bpy.types.Operator):
	bl_idname = 'render.batch_image_target'
	bl_label = 'Assign image target'
	bl_description = "Assign active node in material as target for batch rendering images"
	bl_space_type = "NODE_EDITOR"
	bl_options = {'REGISTER', 'UNDO'}
	
	@classmethod
	def poll(cls, context):
		# Check if necessary object > material > node > node type is selected
		return (
			context.view_layer.objects.active
			and context.view_layer.objects.active.active_material
			and context.view_layer.objects.active.active_material.node_tree.nodes.active
			and context.view_layer.objects.active.active_material.node_tree.nodes.active.type == 'TEX_IMAGE'
		)
	
	def execute(self, context):
		settings = context.scene.render_kit_settings
		
		# Assign active material from active object
		settings.batch_images_material = context.view_layer.objects.active.active_material.name
		# Assign active node from active material from active object
		settings.batch_images_node = context.view_layer.objects.active.active_material.node_tree.nodes.active.name
		return {'FINISHED'}

# Manually set camera and/or render resolution
class batch_camera_update(bpy.types.Operator):
	bl_idname = 'render.batch_camera_update'
	bl_label = 'Update batch rendering camera'
	bl_description = "Update batch rendering camera"
	bl_space_type = "VIEW_3D"
	
	list_offset: bpy.props.IntProperty() = 0
	
	@classmethod
	def poll(cls, context):
		return True
	
	def execute(self, context):
		settings = context.scene.render_kit_settings
		
		# Get current camera
		target_camera = context.scene.camera
		
		# If offset, get previous or next camera from selection or collection
		if self.list_offset != 0:
			# If cameras are selected
			if len(context.selected_objects) > 0 and len([obj for obj in context.selected_objects if obj.type == 'CAMERA']) > 0:
				source_cameras = [obj for obj in context.selected_objects if obj.type == 'CAMERA']
			
			# If no cameras are selected, check for an active collection with cameras
			elif context.view_layer.active_layer_collection and len(context.view_layer.active_layer_collection.collection.all_objects) > 0 and len([obj for obj in context.view_layer.active_layer_collection.collection.all_objects if obj.type == 'CAMERA']) > 0:
				source_cameras = [obj for obj in context.view_layer.active_layer_collection.collection.all_objects if obj.type == 'CAMERA']
			
			# If still no cameras are available, return cancelled
			else:
				settings.batch_active = False
				print('Render Kit Batch: Cameras not found.')
				return {'CANCELLED'}
			
			batch_length = len(source_cameras) - 1
			
			# If active camera is in the current group, offset from that position
			if target_camera in source_cameras:
				index = source_cameras.index(target_camera) + self.list_offset
				if index < 0:
					index += batch_length + 1
				elif index > batch_length:
					index -= batch_length + 1
				
				settings.batch_index = index
				target_camera = source_cameras[index]
			
			# Otherwise start at zero
			else:
				settings.batch_index = 0
				target_camera = source_cameras[0]
			
			# Set batch values
			settings.batch_factor = settings.batch_index / batch_length
			settings.batch_random = hash(settings.batch_factor * 0.9998 + 0.0001) / 1000000 % 1
		
			# Set rendering camera to current camera and make the item active for editing
			context.scene.camera = target_camera
			context.view_layer.objects.active = target_camera
		
		# Set scene resolution from camera name if appended "#x#" pattern is found
		resolution_match = search(r'(\d+)x(\d+)$', context.scene.camera.name)
		if resolution_match != None:
			context.scene.render.resolution_x = int(resolution_match.group(1))
			context.scene.render.resolution_y = int(resolution_match.group(2))
		
		return {'FINISHED'}



###########################################################################
# Batch Render UI
# •Properties > Options > Batch Render Panel
#	•Cameras
#	•Collections
#	•Items (objects and/or lights)
#	•Images (with folder and material node selection)

class BATCH_PT_batch_render(bpy.types.Panel):
	bl_idname = 'BATCH_PT_batch_render'
	bl_label = 'Batch Render'
	bl_description = 'Manage batch rendering options'
	bl_space_type = 'PROPERTIES'
	bl_region_type = 'WINDOW'
	bl_context = "output"
	bl_category = 'Batch'
#	bl_options = {'DEFAULT_CLOSED'}
	bl_order = 4
	
	@classmethod
	def poll(cls, context):
		return context.preferences.addons[__package__].preferences.batch_enable
	
	def draw_header(self, context):
		try:
			layout = self.layout
		except Exception as exc:
			print(str(exc) + ' | Error in Render Kit: Batch Render panel header')
			
	def draw(self, context):
		if True:
			settings = context.scene.render_kit_settings
			
			# UI Layout
			layout = self.layout
			layout.use_property_decorate = False # No animation
			
			# General variables
			batch_count = 0
			batch_error = False
			
			# Batch type
			input0 = layout.column(align=True)
			input0.prop(settings, 'batch_type', text='')
			
			input1 = layout.column(align=True)
			input2 = layout.column(align=True)
			
			# Settings for Cameras
			if settings.batch_type == 'cams':
				# Direct selection of cameras
				batch_count = len([obj for obj in context.selected_objects if obj.type == 'CAMERA'])
				
				# Set up feedback message for selected cameras
				if batch_count > 0:
					if batch_count == 1:
						feedback_text=str(batch_count) + ' camera selected'
					else:
						feedback_text=str(batch_count) + ' cameras selected'
					feedback_icon='CAMERA_DATA' # Alt: VIEW_CAMERA
				
				# If no cameras are selected, check for an active collection
				elif context.view_layer.active_layer_collection and len(context.view_layer.active_layer_collection.collection.all_objects) > 0 and len([obj for obj in context.view_layer.active_layer_collection.collection.all_objects if obj.type == 'CAMERA']) > 0:
					batch_count = len([obj for obj in context.view_layer.active_layer_collection.collection.all_objects if obj.type == 'CAMERA'])
					if batch_count == 1:
						feedback_text=str(batch_count) + ' camera in collection'
					else:
						feedback_text=str(batch_count) + ' cameras in collection'
					feedback_icon='OUTLINER_COLLECTION'
				
				# If still no items are selected, display an error
				else:
					feedback_text='Invalid selection'
					feedback_icon='ERROR'
				
				# Display feedback
				feedback = input0.box()
				feedback.label(text=feedback_text, icon=feedback_icon)
				
				# Display previous / update / next camera buttons
				if feedback_icon != "ERROR":
					buttons = input1.row(align=True)
					
					# Switch to previous camera in list
					if batch_count > 1:
						op0 = buttons.operator(batch_camera_update.bl_idname, text = "Previous", icon = "TRIA_LEFT") # 
						op0.list_offset = -1
					
					# Update current camera
					op1 = buttons.operator(batch_camera_update.bl_idname, text = "Update", icon = "FILE_REFRESH") # 
					op1.list_offset = 0
					
					# Switch to next camera in list
					if batch_count > 1:
						op2 = buttons.operator(batch_camera_update.bl_idname, text = "Next", icon = "TRIA_RIGHT") # 
						op2.list_offset = 1
			
			# Settings for Collections
			if settings.batch_type == 'cols':
				# Collection children (no direct selection of collections currently supported)
				batch_count = len(context.view_layer.active_layer_collection.children)
				
				# Set up feedback message for child collections
				if batch_count > 0:
					if batch_count == 1:
						feedback_text=str(batch_count) + ' sub-collection available'
					else:
						feedback_text=str(batch_count) + ' sub-collections available'
					feedback_icon='OUTLINER_COLLECTION'
				
				# If no collections are available, display an error
				else:
					feedback_text='Invalid selection'
					feedback_icon='ERROR'
				
				# Display feedback
				feedback = input0.box()
				feedback.label(text=feedback_text, icon=feedback_icon)
			
			# Settings for Items
			if settings.batch_type == 'itms':
				# Direct selection of items
				batch_count = len([obj for obj in context.selected_objects if obj.type != 'CAMERA'])
				
				# Set up feedback message for selected items
				if batch_count > 0:
					if batch_count == 1:
						feedback_text=str(batch_count) + ' item selected'
					else:
						feedback_text=str(batch_count) + ' items selected'
					feedback_icon='OBJECT_DATA'
				
				# If no items are selected, check for an active collection
				elif context.view_layer.active_layer_collection and len(context.view_layer.active_layer_collection.collection.all_objects) > 0 and len([obj for obj in context.view_layer.active_layer_collection.collection.all_objects if obj.type != 'CAMERA']) > 0:
					batch_count = len([obj for obj in context.view_layer.active_layer_collection.collection.all_objects if obj.type != 'CAMERA'])
					if batch_count == 1:
						feedback_text=str(batch_count) + ' item in collection'
					else:
						feedback_text=str(batch_count) + ' items in collection'
					feedback_icon='OUTLINER_COLLECTION'
				
				# If still no items are selected, display an error
				else:
					feedback_text='Invalid selection'
					feedback_icon='ERROR'
				
				# Display feedback
				feedback = input0.box()
				feedback.label(text=feedback_text, icon=feedback_icon)
			
			# Settings for Images
			if settings.batch_type == 'imgs':
				# Source directory
				input1.prop(settings, 'batch_images_location', text='')
				
				# Get source folder and image count
				source_folder = bpy.path.abspath(settings.batch_images_location)
				if os.path.isdir(source_folder):
					# Image extensions attribute is undocumented
					# https://blenderartists.org/t/bpy-ops-image-open-supported-formats/1237197/6
					source_images = [f for f in os.listdir(source_folder) if f.lower().endswith(tuple(bpy.path.extensions_image))]
					batch_count = len(source_images)
					feedback_text=str(batch_count) + ' images found'
					feedback_icon='IMAGE_DATA'
				else:
					feedback_text='Invalid location'
					feedback_icon='ERROR'
					batch_error = True
				
				feedback = input1.box()
				feedback.label(text=feedback_text, icon=feedback_icon)
				
				# Material node assignment
				if context.view_layer.objects.active and context.view_layer.objects.active.active_material and context.view_layer.objects.active.active_material.node_tree.nodes.active and context.view_layer.objects.active.active_material.node_tree.nodes.active.type == 'TEX_IMAGE':
					target_text = 'Assign ' + context.view_layer.objects.active.active_material.name + ' > ' + context.view_layer.objects.active.active_material.node_tree.nodes.active.name
					target_icon = 'IMPORT'
				else:
					target_text = 'Assign Image Node'
					target_icon = 'ERROR'
				
				input2.operator(batch_image_target.bl_idname, text=target_text)
				
				# List the assigned material node if it exists
				if bpy.data.materials.get(settings.batch_images_material) and bpy.data.materials[settings.batch_images_material].node_tree.nodes.get(settings.batch_images_node):
					feedback_text = settings.batch_images_material + ' > ' + settings.batch_images_node
					feedback_icon = 'NODE'
				else:
					feedback_text = 'Select object > material > image node'
					feedback_icon = 'ERROR'
					batch_error = True
				
				feedback = input2.box()
				feedback.label(text=feedback_text, icon=feedback_icon)
			
			# Not-really-read-only batch index and values
			field = layout.column(align=True)
			field.label(text="Batch values (set during rendering)")
			field.prop(settings, 'batch_index', text='Index', icon='MODIFIER') # PREFERENCES MODIFIER
			field.prop(settings, 'batch_factor', text='Factor', icon='MODIFIER') # PREFERENCES MODIFIER
			field.prop(settings, 'batch_random', text='Random', icon='MODIFIER') # PREFERENCES MODIFIER
			
			# Final settings and start render
			input3 = layout.column(align=True)
			
			# Batch range setting (still or sequence)
			buttons = input3.row(align=True)
			buttons.prop(settings, 'batch_range', expand = True)
			
			# Start Batch Render button with title feedback
			button = input3.row(align=True)
			if batch_count == 0 or batch_error:
				button.active = False
				button.enabled = False
				batch_text = 'Batch Render'
				batch_icon = 'ERROR'
			else:
				batch_text = 'Batch Render '
				batch_text += str(batch_count)
				if settings.batch_range == 'img':
					batch_text += ' Image'
					batch_icon = 'RENDER_STILL'
				else:
					batch_text += ' Animation'
					batch_icon = 'RENDER_ANIMATION'
				batch_text += 's' if batch_count > 1 else ''
			
			# Start batch render button
			button.operator(batch_render_start.bl_idname, text=batch_text, icon=batch_icon)

###########################################################################
# Menu UI rendering class

def render_batch_menu_item(self,context):
	try:
		if context.preferences.addons[__package__].preferences.batch_enable:
			layout = self.layout
			layout.operator(batch_render_start.bl_idname, text="Render Batch", icon='RENDER_STILL')
	except Exception as exc:
		print(str(exc) + " Render Kit | Error in Topbar Mt Render when adding to menu")
