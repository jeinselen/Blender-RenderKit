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
					"{project}", "{scene}", "{viewlayer}", "{collection}", "{camera}", "{item}", "{material}", "{node}", "{socket}",
				"title,Image,NODE_COMPOSITING",
					"{display}", "{colorspace}", "{look}", "{exposure}", "{gamma}", "{curves}", "{compositing}",
				"title,Render,SCENE",
					"{engine}", "{device}", "{samples}", "{features}", "{duration}", "{rtime}", "{rH},{rM},{rS}",
				"title,System,DESKTOP",
					"{host}", "{processor}", "{platform}", "{system}", "{release}", "{python}", "{blender}",
				"title,Identifier,COPY_ID",
					"{date}", "{y},{m},{d}", "{time}", "{H},{M},{S}", "{serial}", "{frame}", "{batch}"]



###########################################################################
# Variable replacement function
# •Prepopulate data that requires more logic
# •Replace all variables
# 	•Replaces {duration}{rtime}{rH}{rM}{rS} only if valid 0.0+ float is provided
# 	•Replaces {serial} only if valid 0+ integer is provided

def replaceVariables(string, rendertime=-1.0, serial=-1, socket=''):
	scene = bpy.context.scene
	view_layer = bpy.context.view_layer
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
	
	elif bpy.context.engine == 'BLENDER_EEVEE':
		renderEngine = 'Eevee'
		renderDevice = 'GPU'
		renderSamples = str(scene.eevee.taa_render_samples) + '+' + str(scene.eevee.sss_samples) + '+' + str(scene.eevee.volumetric_samples)
		renderFeaturesArray = []
		if scene.eevee.use_gtao:
			renderFeaturesArray.append('AO')
		if scene.eevee.use_bloom:
			renderFeaturesArray.append('Bloom')
		if scene.eevee.use_ssr:
			renderFeaturesArray.append('SSR')
		if scene.eevee.use_motion_blur:
			renderFeaturesArray.append('MB' + str(scene.eevee.motion_blur_steps))
		renderFeatures = 'None' if len(renderFeaturesArray) == 0 else '+'.join(renderFeaturesArray)
	
	elif bpy.context.engine == 'BLENDER_EEVEE':
		renderEngine = 'Eevee'
		renderDevice = 'GPU'
		renderSamples = str(scene.eevee.taa_render_samples) + '+' + str(scene.eevee.shadow_ray_count) + '+' + str(scene.eevee.shadow_step_count) + '+' + str(scene.eevee.volumetric_shadow_samples) + '+' + str(scene.eevee.shadow_resolution_scale)# + '+' + str(scene.eevee.volumetric_samples)
		renderFeaturesArray = []
		if scene.eevee.use_gtao:
			renderFeaturesArray.append('AO')
		if scene.eevee.use_bloom:
			renderFeaturesArray.append('Bloom')
		if scene.eevee.use_ssr:
			renderFeaturesArray.append('SSR')
		if scene.eevee.use_motion_blur:
			renderFeaturesArray.append('MB' + str(scene.eevee.motion_blur_steps))
		renderFeatures = 'None' if len(renderFeaturesArray) == 0 else '+'.join(renderFeaturesArray)
	
	elif bpy.context.engine == 'BLENDER_EEVEE_NEXT':
		renderEngine = 'EeveeNext'
		renderDevice = 'GPU'
		renderSamples = str(scene.eevee.taa_samples) + '+' + str(scene.eevee.use_taa_reprojection) + '+' + str(scene.eevee.use_shadow_jitter_viewport) + '+' + str(scene.eevee.volumetric_tile_size) + '+' + str(scene.eevee.volumetric_samples) + '+' + ("%.2f" % scene.eevee.volumetric_sample_distribution) + '+' + str(scene.eevee.volumetric_ray_depth)
		if scene.eevee.use_raytracing:
			renderFeatures = 'RT+' + str(scene.eevee.ray_tracing_method) + '+' + str(scene.eevee.ray_tracing_options.resolution_scale) + '+' + ("%.2f" % scene.eevee.ray_tracing_options.trace_max_roughness) + '+' + str(scene.eevee.ray_tracing_options.use_denoise) + '+' + str(scene.eevee.ray_tracing_options.denoise_spatial) + '+' + str(scene.eevee.ray_tracing_options.denoise_temporal) + '+' + str(scene.eevee.ray_tracing_options.denoise_bilateral) + '+' + str(scene.eevee.fast_gi_method) + '+' + str(scene.eevee.fast_gi_resolution) + '+' + str(scene.eevee.fast_gi_ray_count) + '+' + str(scene.eevee.fast_gi_step_count) + '+' + ("%.2f" % scene.eevee.fast_gi_quality) + '+' + ("%.2f" % scene.eevee.fast_gi_distance) + '+' + ("%.2f" % (scene.eevee.fast_gi_thickness_far * 57.29577951)) + '+' + ("%.2f" % scene.eevee.fast_gi_bias)
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
		renderDevice = 'None' if len(renderDevicesArray) == 0 else '+'.join(renderDevicesArray)
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
	projectItem = projectMaterial = projectNode = 'None'
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
				
				# Set node variable to image, map, or group source
				if node.type == 'TEX_IMAGE' and node.image:
					projectNode = node.image.name
				elif node.type == 'UVMAP':
					projectNode = node.uv_map
				elif node.type == 'GROUP':
					projectNode = node.node_tree.name
				# Otherwise use the node label
				elif len(node.label) > 0:
					projectNode = node.label
				# If the label is blank, use the math type or node name
				# Note that spaces are only replaced with underscores here, because these last two naming options are not user-defined
				elif node.type == 'MATH' or node.type == 'VECT_MATH':
					projectNode = node.operation.replace("_", " ").title().replace(" ", "_")
				else:
					projectNode = node.name.replace(" ", "_")
				projectNode = sub(r'[<>:"/\\\|?*]+', "-", projectNode) # Sanitised
	
	# Set node name to the Batch Render Target if active and available
	if settings.batch_active and settings.batch_type == 'imgs' and bpy.data.materials.get(settings.batch_images_material) and bpy.data.materials[settings.batch_images_material].node_tree.nodes.get(settings.batch_images_node):
		projectNode = bpy.data.materials[settings.batch_images_material].node_tree.nodes.get(settings.batch_images_node).image.name
	
	# Remove file extension from image node names (this could be unhelpful when comparing renders with .psd versus .jpg texture sources)
	projectNode = sub(r'\.\w{3,4}$', '', projectNode)
	
	# Using "replace" because "format" fails ungracefully when an exact match isn't found
	# Project variables
	string = string.replace("{project}", os.path.splitext(os.path.basename(bpy.data.filepath))[0])
	string = string.replace("{scene}", scene.name)
	string = string.replace("{viewlayer}", view_layer.name)
	string = string.replace("{collection}", settings.batch_collection_name if len(settings.batch_collection_name) > 0 else bpy.context.collection.name)
	string = string.replace("{camera}", scene.camera.name)
	string = string.replace("{item}", projectItem)
	string = string.replace("{material}", projectMaterial)
	string = string.replace("{node}", projectNode)
	if len(socket) > 0: # Only enabled if a value is supplied
		string = string.replace("{socket}", str(socket))
	
	# Image variables
	sceneOverride = scene.render.image_settings if bpy.context.scene.render.image_settings.color_management == "OVERRIDE" else scene
	string = string.replace("{display}", sceneOverride.display_settings.display_device.replace(" ", "").replace(".", ""))
	string = string.replace("{space}", sceneOverride.view_settings.view_transform.replace(" ", ""))
	string = string.replace("{look}", sceneOverride.view_settings.look.replace(" ", "").replace("AgX-", "").replace("FalseColor-", ""))
	string = string.replace("{exposure}", str(sceneOverride.view_settings.exposure))
	string = string.replace("{gamma}", str(sceneOverride.view_settings.gamma))
	string = string.replace("{curves}", "Curves" if sceneOverride.view_settings.use_curve_mapping else "None")
	string = string.replace("{compositing}", "Compositing" if scene.use_nodes else "None")
	
	# Rendering variables
	string = string.replace("{engine}", renderEngine)
	string = string.replace("{device}", renderDevice)
	string = string.replace("{samples}", renderSamples)
	string = string.replace("{features}", renderFeatures)
	if float(rendertime) >= 0.0: # Only enabled if a value is supplied
		string = string.replace("{duration}", str(rendertime) + 's')
		rH, rM, rS = secondsToStrings(rendertime)
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
	string = string.replace("{frame}", format(scene.frame_current, '04'))
	# Consider adding hash-mark support for inserting frames: sub(r'#+(?!.*#)', "", absolute_path)
	# Batch variables
	string = string.replace("{batch}", format(settings.batch_index, '04'))
	
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

# Popup panel UI
class OutputVariablePopup(bpy.types.Operator):
	"""List of the available variables"""
	bl_label = "Variable List"
	bl_idname = "ed.output_variable_popup"
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
			elif (item not in ["{duration}", "{rtime}", "{rH},{rM},{rS}", "{frame}"] or self.postrender) and (item not in ["{socket}"] or self.noderender):
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

# Render output UI
def RENDER_PT_output_path_variable_list(self, context):
	prefs = context.preferences.addons[__package__].preferences
	settings = context.scene.render_kit_settings
	
	if not (False) and prefs.render_output_variables:
		# UI layout for Scene Output
		layout = self.layout
		ops = layout.operator(OutputVariablePopup.bl_idname, text="Variable List", icon="LINENUMBERS_OFF") # LINENUMBERS_OFF, THREE_DOTS, SHORTDISPLAY, ALIGN_JUSTIFY
		ops.postrender = False
		ops.noderender = False
		ops.autoclose = True
		layout.use_property_decorate = False
		layout.use_property_split = True
		input = layout.row()
		if not '{serial}' in bpy.context.scene.render.filepath:
			input.active = False
			input.enabled = False
		input.prop(settings, 'output_file_serial')

# Node output UI
def NODE_PT_output_path_variable_list(self, context):
	prefs = context.preferences.addons[__package__].preferences
	settings = context.scene.render_kit_settings
	
	if not (False) and prefs.render_output_variables and context.scene.node_tree and context.scene.node_tree.type == 'COMPOSITING':
		active_node = context.scene.node_tree.nodes.active
		if isinstance(active_node, bpy.types.CompositorNodeOutputFile):
#		if active_node.type == 'OUTPUT_FILE':
			# Get file path and all output file names from the current active node
			paths = [context.scene.node_tree.nodes.active.base_path]
			for slot in context.scene.node_tree.nodes.active.file_slots:
				paths.append(slot.path)
			paths = ''.join(paths)
			
			# UI layout for Node Properties
			layout = self.layout
			layout.use_property_decorate = False
			layout.use_property_split = True
			ops = layout.operator(OutputVariablePopup.bl_idname, text="Variable List", icon="LINENUMBERS_OFF")
			ops.postrender = False
			ops.noderender = False
			ops.autoclose = True
			input = layout.row()
			if not '{serial}' in paths:
				input.active = False
				input.enabled = False
			input.prop(settings, 'output_file_serial')
			layout.use_property_split = False # Base path interface doesn't specify false, it assumes it, so the UI gets screwed up if we don't reset here
