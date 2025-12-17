# General features
import bpy
import datetime

# File paths
import os
from pathlib import Path

# Variable data
import platform
from re import findall, search, sub

# Internal imports
from .utility_time import secondsToStrings



# Available variables
# Includes both headers (string starting with "title,") and variables (string with brackets, commas segment multi-variable lines)
variableArray = ["title,Project,SCENE_DATA",
					"{{project}}", "{{scene}}", "{{viewlayer}}", "{{collection}}", "{{camera}}", "{{item}}", "{{material}}", "{{node}}", "{{socket}}", "{{marker}}",
				"title,Image,NODE_COMPOSITING",
					"{{display}}", "{{space}}", "{{look}}", "{{exposure}}", "{{gamma}}", "{{curves}}", "{{compositing}}",
				"title,Render,SCENE",
					"{{engine}}", "{{device}}", "{{samples}}", "{{features}}", "{{duration}}", "{{rtime}}", "{{rH}},{{rM}},{{rS}}",
				"title,System,DESKTOP",
					"{{host}}", "{{processor}}", "{{platform}}", "{{system}}", "{{release}}", "{{python}}", "{{blender}}",
				"title,Identifier,COPY_ID",
					"{{date}}", "{{y}},{{m}},{{d}}", "{{time}}", "{{H}},{{M}},{{S}}", "{{serial}}", "{{frame}}", "{{batch}}",
				]



# Available values
# Includes both headers (string starting with "title,") and value properties (string with brackets)
valueArray =   ["title,Scene,SCENE_DATA",
					"{{s0}}", "{{s1}}", "{{s2}}", "{{s3}}", "{{s4}}", "{{s5}}", "{{s6}}", "{{s7}}", "{{s8}}", "{{s9}}",
				"title,View Layer,RENDERLAYERS",
					"{{v0}}", "{{v1}}", "{{v2}}", "{{v3}}", "{{v4}}", "{{v5}}", "{{v6}}", "{{v7}}", "{{v8}}", "{{v9}}",
				"title,Active Item,OBJECT_DATA",
					"{{i0}}", "{{i1}}", "{{i2}}", "{{i3}}", "{{i4}}", "{{i5}}", "{{i6}}", "{{i7}}", "{{i8}}", "{{i9}}",
#				"title,Active Material,MATERIAL",
#					"{{m0}}", "{{m1}}", "{{m2}}", "{{m3}}", "{{m4}}", "{{m5}}", "{{m6}}", "{{m7}}", "{{m8}}", "{{m9}}",
				]

valueName = "RenderKit_Value_"



###########################################################################
# Variable replacement function
# •Prepopulate data that requires more logic
# •Replace all variables
# 	•Replaces {duration}{rtime}{rH}{rM}{rS} only if valid 0.0+ float is provided
# 	•Replaces {serial} only if valid 0+ integer is provided

def replaceVariables(string, render_time=-1.0, serial=-1, socket=''):
	context = bpy.context
	view_layer = context.view_layer
	scene = context.scene
	settings = scene.render_kit_settings
	
	# Get render engine feature sets
	if bpy.context.engine == 'BLENDER_WORKBENCH':
		renderEngine = 'Workbench'
		renderDevice = 'GPU'
		renderSamples = scene.display.render_aa
		renderFeatures = scene.display.shading.light.title().replace("Matcap", "MatCap") + '+' + scene.display.shading.color_type.title()
	
	elif bpy.context.engine == 'HYDRA_STORM':
		renderEngine = 'HydraStorm'
		renderDevice = 'GPU'
		renderSamples = str(scene.hydra_storm.final.max_lights)
		renderFeatures = str(scene.hydra_storm.final.volume_raymarching_step_size) + '+' + str(scene.hydra_storm.final.volume_raymarching_step_size_lighting) + '+' + str(scene.hydra_storm.final.volume_max_texture_memory_per_field)
	
	elif bpy.context.engine in ('BLENDER_EEVEE', 'BLENDER_EEVEE_NEXT'):
		renderEngine = 'Eevee'
		renderDevice = 'GPU'
		
		# General features
		renderSamples = str(scene.eevee.taa_render_samples)
		if scene.eevee.use_shadows:
			renderSamples += '+Shadows'
			renderSamples += '+' + str(scene.eevee.shadow_ray_count)
			renderSamples += '+' + str(scene.eevee.shadow_step_count)
			if scene.eevee.use_volumetric_shadows:
				renderSamples += '+VolumeShadows'
				renderSamples += '+' + str(scene.eevee.volumetric_shadow_samples)
				renderSamples += '+' + str(scene.eevee.shadow_resolution_scale)
		renderSamples += '+Volumes+' + str(scene.eevee.volumetric_tile_size)
		renderSamples += '+' + str(scene.eevee.volumetric_samples)
		renderSamples += '+' + str("%.2f" % scene.eevee.volumetric_sample_distribution)
		renderSamples += '+' + str(scene.eevee.volumetric_ray_depth)
		renderSamples += '+DOF+' + str("%.2f" % scene.eevee.bokeh_max_size) + 'px'
		renderSamples += '+' + str("%.2f" % scene.eevee.bokeh_threshold)
		renderSamples += '+' + str("%.2f" % scene.eevee.bokeh_neighbor_max)
		if scene.eevee.use_bokeh_jittered:
			renderSamples += '+JC+' + str("%.2f" % scene.eevee.bokeh_overblur)
		if scene.render.use_motion_blur:
			renderSamples += '+MB+' + scene.render.motion_blur_position
			renderSamples += '+' + str("%.2f" % scene.render.motion_blur_shutter)
			renderSamples += '+' + str("%.2f" % scene.eevee.motion_blur_depth_scale)
			renderSamples += '+' + str(scene.eevee.motion_blur_max) + 'px'
			renderSamples += '+' + str(scene.eevee.motion_blur_steps)
		if scene.render.use_simplify:
			renderSamples += '+Simplify'
		if scene.render.use_high_quality_normals:
			renderSamples += '+HQN'
		
		# Raytracing specific features
		if scene.eevee.use_raytracing:
			renderFeatures = 'RT'
			renderFeatures += '+' + str(scene.eevee.ray_tracing_method)
			renderFeatures += '+' + str(scene.eevee.ray_tracing_options.resolution_scale)
			renderFeatures += '+' + str("%.2f" % scene.eevee.ray_tracing_options.screen_trace_quality)
			renderFeatures += '+' + str("%.2f" % scene.eevee.ray_tracing_options.screen_trace_thickness) + 'm'
			if scene.eevee.ray_tracing_options.use_denoise:
				renderFeatures += '+DN'
				renderFeatures += '+DS' if scene.eevee.ray_tracing_options.denoise_spatial else ''
				renderFeatures += '+DT' if scene.eevee.ray_tracing_options.denoise_temporal else ''
				renderFeatures += '+DB' if scene.eevee.ray_tracing_options.denoise_bilateral else ''
			if scene.eevee.use_fast_gi:
				renderFeatures += '+FastGI'
				renderFeatures += '+' + str("%.2f" % scene.eevee.ray_tracing_options.trace_max_roughness)
				renderFeatures += '+' + str(scene.eevee.fast_gi_method)
				renderFeatures += '+' + str(scene.eevee.fast_gi_resolution)
				renderFeatures += '+' + str(scene.eevee.fast_gi_ray_count)
				renderFeatures += '+' + str(scene.eevee.fast_gi_step_count)
				renderFeatures += '+' + str("%.2f" % scene.eevee.fast_gi_quality)
				renderFeatures += '+' + str("%.2f" % scene.eevee.fast_gi_distance) + 'm'
				renderFeatures += '+' + str("%.2f" % scene.eevee.fast_gi_thickness_near) + 'm'
				renderFeatures += '+' + str("%.2f" % (scene.eevee.fast_gi_thickness_far * 57.29577951)) + 'd'
				renderFeatures += '+' + str("%.2f" % scene.eevee.fast_gi_bias)
		else:
			renderFeatures = 'NoRT'
	
	elif bpy.context.engine == 'CYCLES':
		renderEngine = 'Cycles'
		renderDevice = scene.cycles.device
		# Add compute device type if GPU is enabled
		# if renderDevice == "GPU":
			# renderDevice += '_' + bpy.context.preferences.addons["cycles"].preferences.compute_device_type
		renderSamples = str(round(scene.cycles.adaptive_threshold, 4)) + '+' + str(scene.cycles.samples) + '+' + str(scene.cycles.adaptive_min_samples)
		renderFeatures = str(scene.cycles.max_bounces) + '+' + str(scene.cycles.diffuse_bounces) + '+' + str(scene.cycles.glossy_bounces) + '+' + str(scene.cycles.transmission_bounces) + '+' + str(scene.cycles.volume_bounces) + '+' + str(scene.cycles.transparent_max_bounces)
	
	elif bpy.context.engine == 'RPR':
		renderEngine = 'ProRender'
		# Compile array of enabled devices
		renderDevicesArray = []
		if bpy.context.preferences.addons["rprblender"].preferences.settings.final_devices.cpu_state:
			renderDevicesArray.append('CPU')
		for gpu in bpy.context.preferences.addons["rprblender"].preferences.settings.final_devices.available_gpu_states:
			if gpu:
				renderDevicesArray.append('GPU')
		renderDevice = 'none' if len(renderDevicesArray) == 0 else '+'.join(renderDevicesArray)
		renderSamples = str(scene.rpr.limits.min_samples) + '+' + str(scene.rpr.limits.max_samples) + '+' + str(round(scene.rpr.limits.noise_threshold, 4))
		renderFeatures = str(scene.rpr.max_ray_depth) + '+' + str(scene.rpr.diffuse_depth) + '+' + str(scene.rpr.glossy_depth) + '+' + str(scene.rpr.refraction_depth) + '+' + str(scene.rpr.glossy_refraction_depth) + '+' + str(scene.rpr.shadow_depth)
	
	elif bpy.context.engine == 'LUXCORE':
		renderEngine = 'LuxCore'
		renderDevice = 'CPU' if scene.luxcore.config.device == 'CPU' else 'GPU'
		# Samples returns the halt conditions for time, samples, and/or noise threshold
		renderSamples = ''
		if scene.luxcore.halt.use_time:
			renderSamples += str(scene.luxcore.halt.time) + 's'
		if scene.luxcore.halt.use_samples:
			if len(renderSamples) > 0:
				renderSamples += '+'
			renderSamples += str(scene.luxcore.halt.samples)
		if scene.luxcore.halt.use_noise_thresh:
			if len(renderSamples) > 0:
				renderSamples += '+'
			renderSamples += str(scene.luxcore.halt.noise_thresh) + '+' + str(scene.luxcore.halt.noise_thresh_warmup) + '+' + str(scene.luxcore.halt.noise_thresh_step)
		# Features include the number of paths or bounces (depending on engine selected) and denoising if enabled
		if scene.luxcore.config.engine == 'PATH':
			renderEngine += '-Path'
			renderFeatures = str(scene.luxcore.config.path.depth_total) + '+' + str(scene.luxcore.config.path.depth_diffuse) + '+' + str(scene.luxcore.config.path.depth_glossy) + '+' + str(scene.luxcore.config.path.depth_specular)
		else:
			renderEngine += '-Bidir'
			renderFeatures = str(scene.luxcore.config.bidir_path_maxdepth) + '+' + str(scene.luxcore.config.bidir_light_maxdepth)
		if scene.luxcore.denoiser.enabled:
			renderFeatures += '+' + str(scene.luxcore.denoiser.type)
	
	else:
		renderEngine = bpy.context.engine
		renderDevice = 'unknown'
		renderSamples = 'unknown'
		renderFeatures = 'unknown'
	
	# Get conditional project variables Item > Material > Node
	projectItem = projectMaterial = projectNode = 'none'
	if view_layer.objects.active:
		# Set active object
		obj = view_layer.objects.active
		
		# Set active object name
		projectItem = sub(r'[<>:"/\\\|?*]+', "-", obj.name) # Sanitise the most commonly problematic filesystem characters (Microsoft Windows is just the worst)
		
		if obj.active_material:
			# Set active material
			mat = obj.active_material
			
			# Set active material slot name
			projectMaterial = sub(r'[<>:"/\\\|?*]+', "-", mat.name) # Sanitised
			
			if mat.use_nodes and mat.node_tree.nodes.active:
				# Set active node tree node
				node = mat.node_tree.nodes.active
				
				# Node label (if set)
				if len(node.label) > 0:
					projectNode = node.label
				# Specific node types
				elif node.type == 'TEX_IMAGE' and node.image:
					projectNode = node.image.name
				elif node.type == 'UVMAP':
					projectNode = node.uv_map
				elif node.type == 'GROUP':
					projectNode = node.node_tree.name
				elif node.type == 'MATH' or node.type == 'VECT_MATH':
					projectNode = node.operation.replace("_", " ").title().replace(" ", "_")
				# Fallback (node name)
				else:
					projectNode = node.name.replace(" ", "_")
				# Spaces are replaced with underscores only for the two naming options that are not user-defined
				projectNode = sub(r'[<>:"/\\\|?*]+', "-", projectNode) # Sanitised
	
	# Set node name to the Batch Render Target if active and available
	if settings.batch_active and settings.batch_type == 'imgs' and bpy.data.materials.get(settings.batch_images_material) and bpy.data.materials[settings.batch_images_material].node_tree.nodes.get(settings.batch_images_node):
		projectNode = bpy.data.materials[settings.batch_images_material].node_tree.nodes.get(settings.batch_images_node).image.name
	
	# Remove file extension from image node names (this could be unhelpful when comparing renders with .psd versus .jpg texture sources)
	projectNode = sub(r'\.\w{3,4}$', '', projectNode)
	
	# Get current frame
	scene_frame = scene.frame_current
	
	# Get marker names if markers exist
	markerName = 'none'
	if len(scene.timeline_markers) > 0:
		if settings.output_marker_direction == 'PREV':
			# Find closest marker at or before current frame
			frame = -100000
			for marker in scene.timeline_markers:
				if marker.frame <= scene_frame and marker.frame > frame:
					frame = marker.frame
					markerName = marker.name
		else:
			# Find closest marker at or following current frame
			frame = 100000
			for marker in scene.timeline_markers:
				if marker.frame >= scene_frame and marker.frame < frame:
					frame = marker.frame
					markerName = marker.name
	
	# Get output serial number if not provided
	if serial < 0:
		serial = settings.output_file_serial
	
	
	
	# Unescape variables
	string = string.replace("{{", "{")
	string = string.replace("}}", "}")
	
	
	
	# Project variables
	string = string.replace("{project}", os.path.splitext(os.path.basename(bpy.data.filepath))[0])
	string = string.replace("{scene}", scene.name)
	string = string.replace("{viewlayer}", view_layer.name)
	string = string.replace("{collection}", settings.batch_collection_name if len(settings.batch_collection_name) > 0 else bpy.context.collection.name)
	string = string.replace("{camera}", scene.camera.name if scene.camera else 'none')
	string = string.replace("{object}", "{item}") # Alternate variable naming convention
	string = string.replace("{item}", projectItem)
	string = string.replace("{material}", projectMaterial)
	string = string.replace("{node}", projectNode)
	if len(socket) > 0: # Only enabled if a value is supplied
		string = string.replace("{socket}", str(socket))
	string = string.replace("{marker}", markerName)
	
	
	
	# Image variables
	sceneOverride = scene.render.image_settings if bpy.context.scene.render.image_settings.color_management == "OVERRIDE" else scene
	string = string.replace("{display}", sceneOverride.display_settings.display_device.replace(" ", "").replace(".", ""))
	string = string.replace("{space}", sceneOverride.view_settings.view_transform.replace(" ", ""))
	string = string.replace("{look}", sceneOverride.view_settings.look.replace(" ", "").replace("AgX-", "").replace("FalseColor-", ""))
	string = string.replace("{exposure}", str(sceneOverride.view_settings.exposure))
	string = string.replace("{gamma}", str(sceneOverride.view_settings.gamma))
	string = string.replace("{curves}", "Curves" if sceneOverride.view_settings.use_curve_mapping else "none")
	string = string.replace("{compositing}", "Compositing" if scene.render.use_compositing else "none")
	
	
	
	# Rendering variables
	string = string.replace("{engine}", renderEngine)
	string = string.replace("{device}", renderDevice)
	string = string.replace("{samples}", renderSamples)
	string = string.replace("{features}", renderFeatures)
	if float(render_time) >= 0.0: # Only enabled if a zero or positive value is supplied
		string = string.replace("{duration}", str(render_time) + 's')
		rH, rM, rS = secondsToStrings(render_time)
		string = string.replace("{rtime}", rH + '-' + rM + '-' + rS)
		string = string.replace("{rH}", rH)
		string = string.replace("{rM}", rM)
		string = string.replace("{rS}", rS)
	
	
	
	# System variables
	string = string.replace("{host}", platform.node().split('.')[0])
	string = string.replace("{processor}", platform.processor()) # Alternate: platform.machine() provides the same information in many cases
	string = string.replace("{platform}", platform.platform())
	string = string.replace("{system}", platform.system().replace("Darwin", "macOS")) # Alternate: {os}
	string = string.replace("{release}", platform.mac_ver()[0] if platform.system() == "Darwin" else platform.release()) # Alternate: {system}
	string = string.replace("{python}", platform.python_version())
	string = string.replace("{blender}", bpy.app.version_string + '-' + bpy.app.version_cycle)
	
	
	
	# Identifier variables
	string = string.replace("{date}", datetime.datetime.now().strftime('%Y-%m-%d'))
	string = string.replace("{year}", "{y}") # Alternative variable
	string = string.replace("{y}", datetime.datetime.now().strftime('%Y'))
	string = string.replace("{month}", "{m}") # Alternative variable
	string = string.replace("{m}", datetime.datetime.now().strftime('%m'))
	string = string.replace("{day}", "{d}") # Alternative variable
	string = string.replace("{d}", datetime.datetime.now().strftime('%d'))
	string = string.replace("{time}", datetime.datetime.now().strftime('%H-%M-%S'))
	string = string.replace("{hour}", "{H}") # Alternative variable
	string = string.replace("{H}", datetime.datetime.now().strftime('%H'))
	string = string.replace("{minute}", "{M}") # Alternative variable
	string = string.replace("{M}", datetime.datetime.now().strftime('%M'))
	string = string.replace("{second}", "{S}") # Alternative variable
	string = string.replace("{S}", datetime.datetime.now().strftime('%S'))
	if serial >= 0: # Only enabled if a value is supplied
		string = string.replace("{serial}", format(serial, '04'))
	string = string.replace("{frame}", format(scene_frame, '04'))
	# Consider adding hash-mark support for inserting frames: sub(r'#+(?!.*#)', "", absolute_path)
	# Batch variables
	string = string.replace("{batch}", format(settings.batch_index, '04'))
	
	
	
	# Value properties
	property_pattern = r"\{\{([a-z])(\d)\}\}"
	def get_property_value(match):
		type = f"{match.group(1)}"
		property = f"{valueName}{match.group(2)}"
		value = ""
		if type == 's' and property in context.scene:
			value = context.scene[property]
		elif type == 'v' and property in context.view_layer:
			value = context.view_layer[property]
		elif (type == 'i' or type == 'o') and context.view_layer.objects.active and property in context.view_layer.objects.active:
			value = context.view_layer.objects.active[property]
		elif type == 'm' and context.view_layer.objects.active and context.view_layer.objects.active.active_material and property in context.view_layer.objects.active.active_material:
			value = context.view_layer.objects.active.active_material[property]
		else:
			value = 'none'
#		value = str(value)
		value = f"{value}"
		value = sub(r'[<>:"/\\\|?*]+', "-", value) # Rudimentary sanitisation, this feature is pretty insecure
		return value
	string = sub(property_pattern, get_property_value, string)
	
	
	
	# And done!
	return string



###########################################################################
# Copy string to clipboard

class CopyVariableToClipboard(bpy.types.Operator):
	"""Copy variable to the clipboard"""
	bl_label = "Copy to clipboard"
	bl_idname = "ed.autosave_render_copy_to_clipboard"
	bl_options = {'REGISTER', 'INTERNAL'}
	
	string: bpy.props.StringProperty()
	close: bpy.props.BoolProperty()
	
	def invoke(self, context, event):
		context.window_manager.clipboard = self.string
		
		if self.close:
			# Close the popup panel by temporarily moving the mouse
			# This appears to be buggy in the Blender 4.2 beta
			x, y = event.mouse_x, event.mouse_y
			context.window.cursor_warp(10, 10)
			move_back = lambda: context.window.cursor_warp(x, y)
			bpy.app.timers.register(move_back, first_interval=0.001)
#			def return_mouse():
#				context.window.cursor_warp(x, y)
#			bpy.app.timers.register(return_mouse, first_interval=0.001)
		
		return {'FINISHED'}



###########################################################################
# Variable info popup and serial number UI
# •Variable list popup panel
# •Add variable list button and serial input at the top of the Render tab > Output panel
# •Add variable list button and serial input at the top of the Compositing workspace > Node tab > Properties panel

# Variable popup panel UI
class VariablePopup(bpy.types.Operator):
	"""List of the available variables"""
	bl_label = "Variables"
	bl_idname = "ed.render_kit_variables_popup"
	bl_options = {'REGISTER', 'INTERNAL'}
	
	postrender: bpy.props.BoolProperty(default=False)
	noderender: bpy.props.BoolProperty(default=False)
	autoclose: bpy.props.BoolProperty(default=True)
	
	@classmethod
	def poll(cls, context):
		return True
	
	def execute(self, context):
		self.report({'INFO'}, "YES")
		return {'FINISHED'}
	
	def invoke(self, context, event):
		return context.window_manager.invoke_popup(self, width=520)
	
	def draw(self, context):
		layout = self.layout
		grid = self.layout.grid_flow(row_major=True, columns = 5, even_columns = True, even_rows = True)
		for item in variableArray:
			# Display headers
			if item.startswith('title,'):
				x = item.split(',')
				col = grid.column()
				col.label(text = x[1], icon = x[2])
			# Display list elements (filtering out time and node socket variables unless specifically enabled)
			elif (item not in ["{{duration}}", "{{rtime}}", "{{rH}},{{rM}},{{rS}}"] or self.postrender) and (item not in ["{{socket}}"] or self.noderender):
				if ',' in item:
					subrow = col.row(align = True)
					for subitem in item.split(','):
						ops = subrow.operator(CopyVariableToClipboard.bl_idname, text=subitem, emboss=False)
						ops.string = subitem
						ops.close = self.autoclose
				else:
					ops = col.operator(CopyVariableToClipboard.bl_idname, text=item, emboss=False)
					ops.string = item
					ops.close = self.autoclose
		layout.label(text='Click a variable to copy it to the clipboard', icon="COPYDOWN") # COPYDOWN PASTEDOWN



class RenderKit_Property_Add(bpy.types.Operator):
	"""Add custom property to the specified target"""
	bl_label = "Add Property"
	bl_idname = "ed.renderkit_property_add"
	bl_options = {'REGISTER', 'INTERNAL'}
	
	target_type: bpy.props.StringProperty()
	target_name: bpy.props.StringProperty()
	prop_name: bpy.props.StringProperty()
#	value: bpy.props.FloatProperty(default=0.0)
	
	def invoke(self, context, event):
		target = None
		if self.target_type == 'SCENE':
			target = context.scene
		elif self.target_type == 'VIEW_LAYER':
			target = context.view_layer
		elif self.target_type == 'OBJECT' and context.view_layer.objects.active:
			target = context.view_layer.objects.active
		elif self.target_type == 'MATERIAL' and context.view_layer.objects.active and context.view_layer.objects.active.active_material:
			target = context.view_layer.objects.active.active_material
		else:
			self.report({'ERROR'}, "Invalid target")
			return {'CANCELLED'}
		
		# Create and set the property
#		set_renderkit_property(target, self.index, self.value)
		target[self.prop_name] = 0.0
		return {'FINISHED'}





###########################################################################
# Value Editor

# Value editing UI
def draw_value_ui(self, context, layout, popup = False, column_count = 1):
	grid = layout.grid_flow(row_major=True, columns = column_count, even_columns = True, even_rows = False)
	
	# Set first-property tracker to ensure just one "add property" button is added to each column
	property_first = False
	
	for item in valueArray:
		
		# Display headers
		if item.startswith('title,'):
			x = item.split(',')
			col = grid.column()
			col.label(text = x[1], icon = x[2])
			# Reset the property button tracking
			property_first = False
			
		# Display list elements
		else:
			# Define target element
			target = None
			target_name = None
			target_type = None
			
			if item.startswith('{s') and context.scene:
				target = context.scene
				target_type = 'SCENE'
				target_path = 'scene'
			elif item.startswith('{v') and context.view_layer:
				target = context.view_layer
				target_type = 'VIEW_LAYER'
				target_path = 'view_layer'
			elif item.startswith('{i') and context.view_layer.objects.active:
				target = context.view_layer.objects.active
				target_type = 'OBJECT'
				target_path = 'view_layer.objects.active'
			elif item.startswith('{m') and bpy.context.view_layer.objects.active.active_material:
				target = context.view_layer.objects.active.active_material
				target_type = 'MATERIAL'
				target_path = 'view_layer.objects.active.active_material'
				
			if target != None:
				target_name = target.name
			
			# Get the property number
			number = sub("\\D", "", item)
			
			# Define the property name
			property_name = f"{valueName}{number}"
			
			# Check for existing property
			if property_name in target:
				# Start a new row in the column
				row = col.row()
				
				# Left side
				split = col.split(factor=0.25, align=True)
				label_row = split.row()
				label_row.alignment = 'RIGHT'
				
				# Copy variable name button
				ops = label_row.operator(CopyVariableToClipboard.bl_idname, text=item, emboss=False)
				ops.string = item
				if popup:
					ops.close = self.autoclose
				
				# Right side
				value_row = split.row(align=True)
				value_column = value_row.column(align=True)
				
				# Display property value field
				value_column.prop(target, f'["{property_name}"]', text="")
				
				# Create sub-row
				operator_row = value_row.row(align=True)
				operator_row.alignment = 'RIGHT'
				
				# Edit property button
				props = operator_row.operator("wm.properties_edit", text="", icon='PREFERENCES', emboss=False)
				props.data_path = target_path
				props.property_name = property_name
				
				# Remove property button
				props = operator_row.operator("wm.properties_remove", text="", icon='X', emboss=False)
				props.data_path = target_path
				props.property_name = property_name
			
			# If no property exists, and no button exists yet, add a button
			elif not property_first:
				row = col.row()
				
				# Add property button
				prop = row.operator(RenderKit_Property_Add.bl_idname, text="Add property variable", icon='ADD', emboss=True)
				prop.target_name = target_name
				prop.target_type = target_type
				prop.prop_name = property_name
				
				# Update property status
				property_first = True



# Value editing popup panel
class ValuePopup(bpy.types.Operator):
	"""List of the available value properties"""
	bl_label = "Values"
	bl_idname = "ed.renderkit_values_popup"
	bl_options = {'REGISTER', 'INTERNAL'}
	
	autoclose: bpy.props.BoolProperty(default=True)
	
	@classmethod
	def poll(cls, context):
		return True
	
	def execute(self, context):
		self.report({'INFO'}, "YES")
		return {'FINISHED'}
	
	def invoke(self, context, event):
		return context.window_manager.invoke_popup(self, width=520)
	
	def draw(self, context):
		layout = self.layout
		draw_value_ui(self, context, layout, popup = True, column_count = 3)
		layout.label(text='Click a variable to copy it to the clipboard', icon="COPYDOWN") # COPYDOWN PASTEDOWN



# Value editing 3D View panel
class RENDER_PT_value_editor_3dview(bpy.types.Panel):
	bl_idname = "RENDER_PT_value_editor_3dview"
	bl_label = "Render Values"
	bl_space_type = 'VIEW_3D'
	bl_region_type = 'UI'
	bl_category = 'Launch'
	bl_order = 31
	bl_options = {'DEFAULT_CLOSED'}
	
	@classmethod
	def poll(cls, context):
		return context.preferences.addons[__package__].preferences.render_variable_enable
	
	def draw(self, context):
		layout = self.layout
		draw_value_ui(self, context, layout, popup = False, column_count = 1)





###########################################################################
# Global variable and values button bar UI panels

def renderkit_variable_ui(layout, context, paths="", postrender=True, noderender=True, autoclose=True, customserial=False, align=False):
	prefs = context.preferences.addons[__package__].preferences
	settings = context.scene.render_kit_settings
	
	# UI layout for Node Properties
#	layout = self.layout
	
	# VARIABLES BAR
	bar = layout.row(align=align)
	
	# Variable list popup button
	ops = bar.operator(VariablePopup.bl_idname, text = "Variables", icon = "LINENUMBERS_OFF")
	ops.postrender = postrender
	ops.noderender = noderender
	ops.autoclose = autoclose
	
	# Value list popup button
	ops = bar.operator(ValuePopup.bl_idname, text = "Values", icon = "PROPERTIES") # PROPERTIES LINENUMBERS_ON
	
	# Local project serial number
	input = bar.column()
	if not '{serial}' in paths:
		input.active = False
		input.enabled = False
	if customserial:
		if prefs.override_autosave_render:
			input.prop(prefs, 'file_serial_global', text='serial')
		else:
			input.prop(settings, 'file_serial', text='serial')
	else:
		input.prop(settings, 'output_file_serial', text='serial')
	
	# Local project marker direction
	option = bar.column()
	if not '{marker}' in paths:
		option.active = False
		option.enabled = False
	option.prop(settings, 'output_marker_direction', text='')



# Render output UI
def RENDER_PT_output_path_variable_list(self, context):
	prefs = context.preferences.addons[__package__].preferences
	settings = context.scene.render_kit_settings
	
	if not (False) and prefs.render_variable_enable:
		# Variable list UI
		renderkit_variable_ui(self.layout, context, paths=bpy.context.scene.render.filepath, postrender=False, noderender=False, autoclose=True)



# Node output UI
def NODE_PT_output_path_variable_list(self, context):
	prefs = context.preferences.addons[__package__].preferences
	settings = context.scene.render_kit_settings
	compositing = context.scene.node_tree if bpy.app.version < tuple([5,0,0]) else context.scene.compositing_node_group
	
	if not (False) and prefs.render_variable_enable and compositing:
		True if tuple([5,0,0]) > bpy.app.version else False
		active_node = compositing.nodes.active
		
		# Check if the node is a File Output node
		if isinstance(active_node, bpy.types.CompositorNodeOutputFile):
			# Get file path and all output file names from the current active node
			# Blender 5.0 changes the naming conventions for compositing trees and file output nodes
			# Old: bpy.context.scene.node_tree.nodes[5].file_slots[3].path
			# New: bpy.context.scene.compositing_node_group.nodes[5].file_output_items[3].name
			if bpy.app.version < tuple([5,0,0]):
				paths = [compositing.nodes.active.base_path]
				output_ports = compositing.nodes.active.file_slots
				for output_port in output_ports:
					paths.append(output_port.path)
				paths = ''.join(paths)
			else:
				paths = [compositing.nodes.active.directory]
				output_ports = compositing.nodes.active.file_output_items
				for output_port in output_ports:
					paths.append(output_port.name)
				paths = ''.join(paths)
			
			# Variable list UI
			renderkit_variable_ui(self.layout, context, paths=paths, postrender=False, noderender=False, autoclose=True)





###########################################################################
# Addon registration functions
# •Define classes being registered
# •Registration function
# •Unregistration function
			
classes = (CopyVariableToClipboard, RenderKit_Property_Add, VariablePopup, ValuePopup, RENDER_PT_value_editor_3dview)

def register():
	# Register classes
	for cls in classes:
		bpy.utils.register_class(cls)
	
	# Add variable popup UI
	bpy.types.RENDER_PT_output.prepend(RENDER_PT_output_path_variable_list)
	bpy.types.NODE_PT_active_node_properties.prepend(NODE_PT_output_path_variable_list)

def unregister():
	# Remove variable popup UI
	bpy.types.NODE_PT_active_node_properties.remove(NODE_PT_output_path_variable_list)
	bpy.types.RENDER_PT_output.remove(RENDER_PT_output_path_variable_list)
	
	# Deregister classes
	for cls in reversed(classes):
		bpy.utils.unregister_class(cls)

if __package__ == "__main__":
	register()