# General features
import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, StringProperty

# FFmpeg system access
from shutil import which

# Local imports
from .render_0_start import render_kit_start
from .render_1_frame import render_kit_frame_pre, render_kit_frame_post
from .render_2_end import render_kit_end
from .render_autosave import RENDER_PT_autosave_video, RENDER_PT_autosave_image
from . import render_batch
from .render_display import RENDER_PT_total_render_time_display, image_viewer_feedback_display
from . import render_node
from .render_proxy import render_proxy_start, render_proxy_menu_item
from .render_region import RENDER_PT_render_region
from . import render_remote
from . import render_variables



###########################################################################
# Global user preferences and UI rendering class

class RenderKitPreferences(bpy.types.AddonPreferences):
	bl_idname = __package__
	
	########## Render Region, Proxy, Batch ##########
	
	# Render region
	region_enable: BoolProperty(
		name='Render Region',
		description='Adds numerical render region controls to the Properties > Output > Format panel',
		default=True)
	
	# Batch rendering
	batch_enable: BoolProperty(
		name='Render Batch',
		description='Adds batch rendering panel to the Properties > Output section, the specified 3D View category, and rendering menu',
		default=True)
	
	def update_batch_category(self, context):
		category = bpy.context.preferences.addons[__package__].preferences.batch_category
		try:
			bpy.utils.unregister_class(render_batch.BATCH_PT_batch_render_3dview)
		except RuntimeError:
			pass
		if len(category) > 0:
			render_batch.BATCH_PT_batch_render_3dview.bl_category = category
			bpy.utils.register_class(render_batch.BATCH_PT_batch_render_3dview)
	
	batch_category: StringProperty(
		name="Batch Render Panel",
		description="Choose a category tab for the panel to be placed in",
		default="Launch",
		update=update_batch_category)
		# Consider adding search_options=(list of currently available tabs) for easier operation
	
	# Render node
	rendernode_enable: BoolProperty(
		name='Render Node',
		description='Adds node baking to the Node Properties panel of the material editor',
		default=True)
	
	# Render node settings (auto process output images)
	magick_location: StringProperty(
		name="ImageMagick location",
		description="System location where the the ImageMagick command line interface is installed",
		default="/opt/local/bin/magick",
		maxlen=4096,
		update=lambda self, context: self.check_magick_location())
	magick_location_previous: StringProperty(default="")
	magick_exists: BoolProperty(
		name="ImageMagick exists",
		description='Stores the existence of ImageMagick at the defined system location',
		default=False)
	
	# Validate the ImageMagick location string on value change and plugin registration
	def check_magick_location(self):
		# Ensure it points at ImageMagick
		if not self.magick_location.endswith('magick'):
			self.magick_location = self.magick_location + 'magick'
		# Test if it's a valid path and replace with valid path if such exists
		if self.magick_location != self.magick_location_previous:
			if which(self.magick_location) is None:
				if which("magick") is None:
					self.magick_exists = False
				else:
					self.magick_location = which("magick")
					self.magick_exists = True
			else:
				self.magick_exists = True
			self.magick_location_previous = self.magick_location
	
	# Proxy render
	proxy_enable: BoolProperty(
		name='Render Proxy',
		description='Adds a proxy rendering option to the rendering menu',
		default=True)
	proxy_show_settings: BoolProperty(
		name='Proxy Settings   ',
		description='Shows proxy rendering options in the preferences panel',
		default=False)
	
	# Proxy render engine overrides
	proxy_renderEngine: EnumProperty(
		name='Render Engine',
		description='Render engine to use for proxy renders',
		items=[
			('BLENDER_WORKBENCH', 'Workbench', 'Use the Workbench render engine for proxy animations'),
			('BLENDER_EEVEE', 'Eevee', 'Use the Eevee render engine for proxy animations'),
			],
		default='BLENDER_WORKBENCH')
	proxy_renderSamples: IntProperty(
		name="Render Samples",
		description="Render engine to use for proxy renders",
		default=16)
	proxy_format: EnumProperty(
		name='File Format',
		description='Image format used for the proxy render files',
		items=[
			('SCENE', 'Project Setting', 'Same format as set in output panel'),
			('PNG', 'PNG', 'Save as png'),
			('JPEG', 'JPEG', 'Save as jpeg'),
			('OPEN_EXR_MULTILAYER', 'OpenEXR MultiLayer', 'Save as multilayer exr'),
			],
		default='JPEG')
	proxy_resolutionMultiplier: IntProperty(
		name="Resolution Multiplier",
		description="Render engine to use for proxy renders",
		default=100)
	proxy_compositing: EnumProperty(
		name='Node Compositing',
		description='Image format used for the proxy render files',
		items=[
			('SCENE', 'Project Setting', 'Same setting as the project'),
			('ON', 'Node Compositing On', 'Force node compositing on when rendering proxies'),
			('OFF', 'Node Compositing Off', 'Force node compositing off when rendering proxies'),
			],
		default='OFF')
	
	# Render remote
	remote_enable: BoolProperty(
		name='Render Remote',
		description='Implements remote rendering on a target Blender instance over the local network',
		default=True)
	remote_show_settings: BoolProperty(
		name='Remote Settings   ',
		description='Shows remote rendering options in the preferences panel',
		default=False)
	
	def update_remote_category(self, context):
		category = bpy.context.preferences.addons[__package__].preferences.remote_category
		try:
			bpy.utils.unregister_class(render_remote.REMOTERENDER_PT_MainPanel)
		except RuntimeError:
			pass
		if len(category) > 0:
			render_remote.REMOTERENDER_PT_MainPanel.bl_category = category
			bpy.utils.register_class(render_remote.REMOTERENDER_PT_MainPanel)
	
	remote_category: StringProperty(
		name="Remote Render Panel",
		description="Choose a category tab for the panel to be placed in",
		default="Launch",
		update=update_remote_category)
		# Consider adding search_options=(list of currently available tabs) for easier operation
	
	# Render remote settings
	remote_cache_directory: StringProperty(
		name="Cache Directory",
		subtype='DIR_PATH',
		default="//remote_render_cache",
		description="Local directory for caching remote projects"
	)
	remote_discovery_port: IntProperty(
		name="Discovery Port",
		default=5001,
		min=1024,
		max=65535,
		description="Port for network discovery"
	)
	remote_communication_port: IntProperty(
		name="Communication Port", 
		default=5002,
		min=1024,
		max=65535,
		description="Port for secure communication"
	)
	remote_passcode: StringProperty(
		name="Authentication Passcode",
		description="Global passcode required for connections to this computer (leave empty for no authentication)",
		subtype='PASSWORD',
		default=""
	)
	
	
	
	########## Render Variables and Autosave ##########
	
	# Render variables
	render_variable_enable: BoolProperty(
		name='Render Variables',
		description='Implements dynamic keywords in the Output directory and Compositing tab "File Output" nodes',
		default=True)
	
	def update_variable_category(self, context):
		category = bpy.context.preferences.addons[__package__].preferences.variable_category
		try:
			bpy.utils.unregister_class(render_variables.RENDER_PT_value_editor_3dview)
		except RuntimeError:
			pass
		if len(category) > 0:
			render_variables.RENDER_PT_value_editor_3dview.bl_category = category
			bpy.utils.register_class(render_variables.RENDER_PT_value_editor_3dview)
	
	variable_category: StringProperty(
		name="Batch Render Panel",
		description="Choose a category tab for the panel to be placed in",
		default="Launch",
		update=update_variable_category)
		# Consider adding search_options=(list of currently available tabs) for easier operation
	
	# Autosave videos
	ffmpeg_processing: BoolProperty(
		name='Autosave Videos',
		description='Enables FFmpeg image sequence compilation options in the Output panel',
		default=True)
	ffmpeg_location: StringProperty(
		name="FFmpeg location",
		description="System location where the the FFmpeg command line interface is installed",
		default="/opt/local/bin/ffmpeg",
		maxlen=4096,
		update=lambda self, context: self.check_ffmpeg_location())
	ffmpeg_location_previous: StringProperty(default="")
	ffmpeg_exists: BoolProperty(
		name="FFmpeg exists",
		description='Stores the existence of FFmpeg at the defined system location',
		default=False)
	
	# Validate the ffmpeg location string on value change and plugin registration
	def check_ffmpeg_location(self):
		# Ensure it points at ffmpeg
		if not self.ffmpeg_location.endswith('ffmpeg'):
			self.ffmpeg_location = self.ffmpeg_location + 'ffmpeg'
		# Test if it's a valid path and replace with valid path if such exists
		if self.ffmpeg_location != self.ffmpeg_location_previous:
			if which(self.ffmpeg_location) is None:
				if which("ffmpeg") is None:
					self.ffmpeg_exists = False
				else:
					self.ffmpeg_location = which("ffmpeg")
					self.ffmpeg_exists = True
			else:
				self.ffmpeg_exists = True
			self.ffmpeg_location_previous = self.ffmpeg_location
	
	# Autosave images
	enable_autosave_render: BoolProperty(
		name="Autosave Images",
		description="Automatically saves numbered or dated images in a directory alongside the project file or in a custom location",
		default=True)
	
	# Override individual project autosave location and file name settings
	override_autosave_render: BoolProperty(
		name="Global Overrides",
		description="Show available global overrides, replacing local project settings",
		default=False)
	file_location_global: StringProperty(
		name="Global File Location",
		description="Leave a single forward slash to auto generate folders alongside project files",
		default="/",
		maxlen=4096,
		subtype="DIR_PATH")
	file_name_type_global: EnumProperty(
		name='Global File Name',
		description='Autosaves files with the project name and serial number, project name and date, or custom naming pattern',
		items=[
			('SERIAL', 'Project Name + Serial Number', 'Save files with a sequential serial number'),
			('DATE', 'Project Name + Date & Time', 'Save files with the local date and time'),
			('RENDER', 'Project Name + Render Engine + Render Time', 'Save files with the render engine and render time'),
			('CUSTOM', 'Custom String', 'Save files with a custom string format'),
			],
		default='SERIAL')
	file_name_custom_global: StringProperty(
		name="Global Custom String",
		description="Format a custom string using the variables listed below",
		default="{project}-{serial}",
		maxlen=4096)
	file_serial_global: IntProperty(
		name="Global Serial Number",
		description="Current serial number, automatically increments with every render (must be manually updated when installing a plugin update)")
	file_format_global: EnumProperty(
		name='Global File Format',
		description='Image format used for the automatically saved render files',
		items=[
			('SCENE', 'Project Setting', 'Same format as set in output panel'),
			('PNG', 'PNG', 'Save as png'),
			('JPEG', 'JPEG', 'Save as jpeg'),
			('OPEN_EXR', 'OpenEXR', 'Save as exr'),
			],
		default='PNG')
	
	
	
	########## Render Time Tracking ##########
	
	show_estimated_render_time: BoolProperty(
		name="Show Estimated Render Time",
		description='Adds estimated remaining render time display to the image editor menu bar while rendering',
		default=True)
	show_total_render_time: BoolProperty(
		name="Show Project Render Time",
		description='Displays the total time spent rendering a project in the output panel',
		default=True)
	external_render_time: BoolProperty(
		name="Save External Render Time Log",
		description='Saves the total time spent rendering to an external log file',
		default=True)
	external_log_name: StringProperty(
		name="File Name",
		description="Log file name; use {project} for per-project tracking, remove it for per-directory tracking",
#		default="{project}-TotalRenderTime.txt",
		default="RenderKit-TotalTime.txt",
		maxlen=4096)
	
	
	
	########## Render Completed Notifications ##########
	
	minimum_time: IntProperty(
		name="Minimum Render Time",
		description="Minimum rendering time required before notifications will be enabled, in seconds",
		default=300)
	
	# Email notifications
	email_enable: BoolProperty(
		name='Email Notification',
		description='Enable email notifications',
		default=False)
	email_server: StringProperty(
		name="SMTP Server",
		description="SMTP server address",
		default="smtp.gmail.com",
		maxlen=64)
	email_port: IntProperty(
		name="SMTP Port",
		description="Port number used by the SMTP server",
		default=465)
	email_from: StringProperty(
		name="Username",
		description="Email address of the account emails will be sent from",
		default="user@gmail.com",
		maxlen=64)
	email_password: StringProperty(
		name="Password",
		description="Password of the account emails will be sent from (Gmail accounts require 2FA and a custom single-use App Password)",
		default="password",
		subtype="PASSWORD")
	email_to: StringProperty(
		name="Recipients",
		description="Comma separated list of recipient addresses, use https://freecarrierlookup.com/ to get the correct address for text messages",
		default="email@server.com, 1234567890@carrier.net",
		maxlen=1024)
	email_subject: StringProperty(
		name="Email Subject",
		description="Text string sent as the email subject line",
		default="{project} rendering completed",
		maxlen=1024)
	email_message: StringProperty(
		name="Email Body",
		description="Text string sent as the email body copy",
		default="{project} rendering completed in {rH}:{rM}:{rS} on {host}",
		maxlen=4096)
	
	# Pushover app notifications
	pushover_enable: BoolProperty(
		name='Pushover Notification',
		description='Enable Pushover mobile device push notifications (requires non-subscription app and user account https://pushover.net/)',
		default=False)
	pushover_key: StringProperty(
		name="Pushover User Key",
		description="Pushover user key, available after setting up a user account",
		default="EnterUserKeyHere",
		maxlen=64)
	pushover_app: StringProperty(
		name="Pushover App Token",
		description="Pushover application token, available after setting up a custom application",
		default="EnterAppTokenHere",
		maxlen=64)
	pushover_subject: StringProperty(
		name="Pushover Title",
		description="Notification title that will be sent to Pushover devices",
		default="{project} rendering completed",
		maxlen=1024)
	pushover_message: StringProperty(
		name="Pushover Message",
		description="Notification message that will be sent to Pushover devices",
		default="{project} rendering completed in {rH}:{rM}:{rS} on {host}",
		maxlen=4096)
	
	# MacOS Siri text-to-speech announcement
	voice_enable: BoolProperty(
		name='Siri Announcement',
		description='Enable MacOS Siri text-to-speech announcements',
		default=False)
	voice_exists: BoolProperty(
		name="MacOS Say exists",
		description='Stores the existence of MacOS Say',
		default=False)
	voice_message: StringProperty(
		name="Siri Message",
		description="Message that Siri will read out loud",
		default="{project} rendering completed in {rH} hours, {rM} minutes, and {rS} seconds",
		maxlen=2048)
	
	# Validate MacOS Say location on plugin registration
	def check_voice_location(self):
		self.voice_exists = False if which('say') is None else True
		
	
	
	############################## Preferences UI ##############################
	
	# User Interface
	def draw(self, context):
		settings = context.scene.render_kit_settings
		
		layout = self.layout
		
		########## Render Region, Render Batch, Render Proxy, Render Node ##########
		
		layout.label(text="General", icon="PREFERENCES") # TOOL_SETTINGS SETTINGS PREFERENCES
		grid0 = layout.grid_flow(row_major=True, columns=2, even_columns=True, even_rows=False, align=False)
		
		grid0.prop(self, "region_enable")
		grid0.separator()
		grid0.prop(self, "batch_enable")
		input = grid0.grid_flow(row_major=True, columns=2, even_columns=True, even_rows=False, align=False)
		if not self.batch_enable:
			input.active = False
			input.enabled = False
		input.prop(self, "batch_category", text="")
		input.separator()
		
		# Render Node settings
		grid0.prop(self, "rendernode_enable")
		input = grid0.grid_flow(row_major=True, columns=2, even_columns=True, even_rows=False, align=False)
		if not self.rendernode_enable:
			input.active = False
			input.enabled = False
		
		# ImageMagick settings
		input.prop(self, "magick_location", text="")
		# Location exists success/fail
		if self.magick_exists:
			input.label(text="✔︎ installed")
		else:
			input.label(text="✘ missing")
		
		# Proxy settings
		grid0.prop(self, "proxy_enable")
		input = grid0.grid_flow(row_major=True, columns=2, even_columns=True, even_rows=False, align=False)
		if not self.proxy_enable:
			input.active = False
			input.enabled = False
			input.prop(self, "proxy_show_settings", icon = "DISCLOSURE_TRI_RIGHT", emboss = False)
		elif self.proxy_show_settings:
			input.prop(self, "proxy_show_settings", icon = "DISCLOSURE_TRI_DOWN", emboss = False)
		else:
			input.prop(self, "proxy_show_settings", icon = "DISCLOSURE_TRI_RIGHT", emboss = False)
		input.separator()
		
		if self.proxy_enable and self.proxy_show_settings:
			# Subgrid Layout
			margin = layout.row()
			margin.separator(factor=2.0)
			subgrid = margin.grid_flow(row_major=True, columns=2, even_columns=True, even_rows=False, align=False)
			margin.separator(factor=2.0)
			
			subgrid.prop(self, "proxy_renderEngine", text="")
			if self.proxy_renderEngine == "BLENDER_EEVEE":
				subgrid.prop(self, "proxy_renderSamples")
			else:
				subgrid.prop(context.scene.display, "render_aa", text="")
			subgrid.prop(self, "proxy_compositing", text="")
			subgrid.prop(self, "proxy_resolutionMultiplier")
			subgrid.prop(self, "proxy_format", text="")
		
		# Remote settings
		grid0.prop(self, "remote_enable")
		input = grid0.grid_flow(row_major=True, columns=2, even_columns=True, even_rows=False, align=False)
		if not self.remote_enable:
			input.active = False
			input.enabled = False
			input.prop(self, "remote_show_settings", icon = "DISCLOSURE_TRI_RIGHT", emboss = False)
		elif self.remote_show_settings:
			input.prop(self, "remote_show_settings", icon = "DISCLOSURE_TRI_DOWN", emboss = False)
		else:
			input.prop(self, "remote_show_settings", icon = "DISCLOSURE_TRI_RIGHT", emboss = False)
		input.separator()
		
		if self.remote_enable and self.remote_show_settings:
			# Subgrid Layout
			margin = layout.row()
			margin.separator(factor=2.0)
			subgrid = margin.grid_flow(row_major=True, columns=2, even_columns=True, even_rows=False, align=False)
			margin.separator(factor=2.0)
			
			subgrid.prop(self, "remote_cache_directory", text="")
			subgrid.prop(self, "remote_passcode", text="")
			subgrid.operator("render_remote.clear_cache", icon='TRASH')
			subgrid.prop(self, "remote_discovery_port", text="")
			subgrid.separator()
			subgrid.prop(self, "remote_communication_port", text="")
			# subgrid.label(text="Leave passcode empty to allow connections without authentication", icon='INFO')
		
		
		
		########## Output Variables ##########
		
		layout.separator(factor = 2.0)
		layout.label(text="Saving", icon="FILE_FOLDER") # CURRENT_FILE FILE_CACHE FILE_FOLDER FILEBROWSER
		grid1 = layout.grid_flow(row_major=True, columns=2, even_columns=True, even_rows=False, align=False)
		
		grid1.prop(self, "render_variable_enable")
		input = grid1.grid_flow(row_major=True, columns=1, even_columns=True, even_rows=False, align=False)
		if not self.render_variable_enable:
			input.active = False
			input.enabled = False
		input.prop(self, "variable_category", text="")
		
		
		
		########## Autosave Videos ##########
		
		grid1.prop(self, "ffmpeg_processing")
		input = grid1.grid_flow(row_major=True, columns=2, even_columns=True, even_rows=False, align=False)
		if not self.ffmpeg_processing:
			input.active = False
			input.enabled = False
		input.prop(self, "ffmpeg_location", text="")
		# Location exists success/fail
		if self.ffmpeg_exists:
			input.label(text="✔︎ installed")
		else:
			input.label(text="✘ missing")
		
		
		
		########## Autosave Images ##########
		
		grid1.prop(self, "enable_autosave_render")
		grid1.prop(self, "override_autosave_render")
		
		# Global Overrides
		if self.enable_autosave_render and self.override_autosave_render:
			# Subgrid Layout
			margin = layout.row()
			margin.separator(factor=2.0)
			subgrid = margin.grid_flow(row_major=True, columns=2, even_columns=True, even_rows=False, align=False)
			margin.separator(factor=2.0)
			
			# File location
			subgrid.separator_spacer()
			input = subgrid.column(align=True)
			input.prop(self, "file_location_global", text='')
			# Display global serial number if used
			if '{serial}' in self.file_location_global:
				input.prop(self, "file_serial_global")
				input.separator()
			
			# File name
			subgrid.separator_spacer()
			input = subgrid.column(align=True)
			input.prop(self, "file_name_type_global", text='', icon='FILE_TEXT')
			if (self.file_name_type_global == 'CUSTOM'):
				input.prop(self, "file_name_custom_global", text='')
				if self.file_name_type_global == 'CUSTOM' and '{serial}' in self.file_name_custom_global:
					input.prop(self, "file_serial_global")
				input.separator()
			
			# File format
			subgrid.separator_spacer()
			input = subgrid.column()
			input.prop(self, "file_format_global", text='', icon='FILE_IMAGE')
			if self.file_format_global == 'SCENE' and context.scene.render.image_settings.file_format == 'OPEN_EXR_MULTILAYER':
				error = input.box()
				error.label(text="Python API can only save single layer EXR files")
				error.label(text="Report: https://developer.blender.org/T71087")
		
		
		
		########## Render Time Data ##########
		
		layout.separator(factor = 2.0)
		layout.label(text="Time", icon="TIME") # TIME MOD_TIME SORTTIME PREVIEW_RANGE
		grid2 = layout.grid_flow(row_major=True, columns=2, even_columns=True, even_rows=False, align=False)
		
		# Render time preferences
		grid2.prop(self, "show_estimated_render_time")
		grid2.separator()
		
		grid2.prop(self, "show_total_render_time")
		input = grid2.column()
		if not self.show_total_render_time:
			input.active = False
			input.enabled = False
		input.prop(settings, 'total_render_time')
		
		grid2.prop(self, "external_render_time")
		input = grid2.column()
		if not self.external_render_time:
			input.active = False
			input.enabled = False
		input.prop(self, "external_log_name", text='')
		
		
		
		########## Render Completed Notifications ##########
		
		layout.separator(factor = 2.0)
		layout.label(text="Alerts", icon="ERROR") # ERROR RECOVER_LAST
		grid3 = layout.grid_flow(row_major=True, columns=1, even_columns=True, even_rows=False, align=False)
		
		# Minimum render time before notifications are enabled
		row1 = grid3.grid_flow(row_major=True, columns=2, even_columns=True, even_rows=False, align=False)
		row1.label(text="Render Completed Notifications")
		row1.prop(self, "minimum_time", icon="TIME")
		
		# Email notifications
		grid3.prop(self, "email_enable")
		if self.email_enable:
			# Subgrid Layout
			margin = grid3.row()
			margin.separator(factor=2.0)
			subgrid = margin.column()
			margin.separator(factor=2.0)
			
			# Security Warning
			box = subgrid.box()
			warning = box.column(align=True)
			warning.label(text="WARNING:")
			warning.label(text="Blender does not encrypt settings and stores credentials as plain text,")
			warning.label(text="account details entered here are NOT SECURED in the file system")
			
			# Account
			settings1 = subgrid.grid_flow(row_major=True, columns=2, even_columns=True, even_rows=False, align=False)
			column1 = settings1.column(align=True)
			column1.label(text="Server")
			column1.prop(self, "email_server", text="", icon="EXPORT")
			column1.prop(self, "email_port")
			column2 = settings1.column(align=True)
			column2.label(text="Account")
			column2.prop(self, "email_from", text="", icon="USER")
			column2.prop(self, "email_password", text="", icon="LOCKED")
			
			# Message
			subgrid.separator(factor=0.5)
			settings2 = subgrid.column(align=True)
			settings2.label(text="Message")
			settings2.prop(self, "email_to", text="", icon="USER")
			settings2.prop(self, "email_subject", text="", icon="FILE_TEXT")
			settings2.prop(self, "email_message", text="", icon="ALIGN_JUSTIFY")
			
			# Spacing
			subgrid.separator(factor=2.0)
		
		# Pushover notifications
		grid3.prop(self, "pushover_enable")
		if self.pushover_enable:
			# Subgrid Layout
			margin = grid3.row()
			margin.separator(factor=2.0)
			subgrid = margin.column()
			margin.separator(factor=2.0)
			
			# Security Warning
			box = subgrid.box()
			warning = box.column(align=True)
			warning.label(text="WARNING:")
			warning.label(text="Blender does not encrypt settings and stores credentials as plain text,")
			warning.label(text="API keys entered here are NOT SECURED in the file system")
			
			# Account
			settings1 = subgrid.column(align=True)
			settings1.label(text="Account")
			row = settings1.grid_flow(row_major=True, columns=2, even_columns=True, even_rows=False, align=True)
			row.prop(self, "pushover_key", text="", icon="USER")
			row.prop(self, "pushover_app", text="", icon="MODIFIER_DATA")
			
			if self.pushover_enable and (len(self.pushover_key) != 30 or len(self.pushover_app) != 30):
				warning = settings1.box()
				warning.label(text='Please enter 30-character API strings for both user key and app token', icon="ERROR")
			
			# Message
			subgrid.separator(factor=0.5)
			settings2 = subgrid.column(align=True)
			settings2.label(text="Message")
			settings2.prop(self, "pushover_subject", text="", icon="FILE_TEXT")
			settings2.prop(self, "pushover_message", text="", icon="ALIGN_JUSTIFY")
			
			# Spacing
			subgrid.separator(factor = 2.0)
		
		# Apple MacOS Siri text-to-speech announcement
		if self.voice_exists:
			grid3.prop(self, "voice_enable")
			if self.voice_enable:
				# Subgrid Layout
				margin = grid3.row()
				margin.separator(factor=2.0)
				subgrid = margin.column()
				margin.separator(factor=2.0)
				
				# Message
				subgrid.prop(self, "voice_message", text='', icon="PLAY_SOUND")



###########################################################################
# Local project settings

class RenderKitSettings(bpy.types.PropertyGroup):
	# Variables for autosave images
	file_location: StringProperty(
		name="File Location",
		description="Leave a single forward slash to auto generate folders alongside project files",
		default="/",
		maxlen=4096,
		subtype="DIR_PATH")
	file_name_type: EnumProperty(
		name='File Name',
		description='Autosaves files with the project name and serial number, project name and date, or custom naming pattern',
		items=[
			('SERIAL', 'Project Name + Serial Number', 'Save files with a sequential serial number'),
			('DATE', 'Project Name + Date & Time', 'Save files with the local date and time'),
			('RENDER', 'Project Name + Render Engine + Render Time', 'Save files with the render engine and render time'),
			('CUSTOM', 'Custom String', 'Save files with a custom string format'),
			],
		default='SERIAL')
	file_name_custom: StringProperty(
		name="Custom String",
		description="Format a custom string using the variables listed below",
		default="{project}-{serial}-{engine}-{duration}",
		maxlen=4096)
	file_serial: IntProperty(
		name="Serial Number",
		description="Current serial number, automatically increments with every render")
	file_format: EnumProperty(
		name='File Format',
		description='Image format used for the automatically saved render files',
		items=[
			('SCENE', 'Project Setting', 'Same format as set in output panel'),
			('PNG', 'PNG', 'Save as png'),
			('JPEG', 'JPEG', 'Save as jpeg'),
			('OPEN_EXR', 'OpenEXR', 'Save as exr'),
			],
		default='JPEG')
	
	# Variables for render time calculation
	start_date: StringProperty(
		name="Render Start Date",
		description="Stores the date when rendering started in seconds as a string",
		default="")
	total_render_time: FloatProperty(
		name="Total Render Time",
		description="Stores the total time spent rendering in seconds",
		default=0)
	
	# Variables for render time estimation
	estimated_render_time_active: BoolProperty(
		name="Render Active",
		description="Indicates if rendering is currently active",
		default=False)
	estimated_render_time_frame: IntProperty(
		name="Starting frame",
		description="Saves the starting frame when render begins (helps correctly estimate partial renders)",
		default=0)
	estimated_render_time_value: StringProperty(
		name="Estimated Render Time",
		description="Stores the estimated time remaining to render",
		default="0:00:00.00")
	
	# Variables for output file path processing
	output_file_path: StringProperty(
		name="Original Render Path",
		description="Stores the original render path as a string to allow for successful restoration after rendering completes",
		default="")
	output_file_nodes: StringProperty(
		name="Original Node Path",
		description="Stores the original node path as a string to allow for successful restoration after rendering completes",
		default="")
	output_file_serial: IntProperty(
		name="Serial Number",
		description="Current serial number, automatically increments with every render")
	output_file_serial_used: BoolProperty(
		name="Output Serial Number Used",
		description="Indicates if any of the output modules use the {serial} variable",
		default=False)
	output_marker_direction: EnumProperty(
		name='Marker Direction',
		description='Use previous or next marker name for the {marker} variable',
		items=[
			('PREV', 'Previous Marker', 'Look behind: the nearest marker before the current frame number'),
			('NEXT', 'Next Marker', 'Look ahead; the nearest marker after the current frame number'),
			],
		default='NEXT')
	
	# Sequence rendering status (used by FFmpeg compilation and estimated time remaining)
	sequence_rendering_status: BoolProperty(
		name="Sequence Active",
		description="Indicates if a sequence is being rendering to ensure FFmpeg is enabled only when more than one frame has been rendered",
		default=False)
	
	# FFmpeg image sequence compilation
	autosave_video_render_path: StringProperty(
		name="Output Path",
		description="Track the output path during rendering in order to support multi-segment timelines",
		default="")
	autosave_video_prores_path: StringProperty(
		name="ProRes Path",
		description="Track the output path during rendering in order to support multi-segment timelines",
		default="")
	autosave_video_mp4_path: StringProperty(
		name="MP4 Path",
		description="Track the output path during rendering in order to support multi-segment timelines",
		default="")
	autosave_video_custom_path: StringProperty(
		name="Custom Path",
		description="Track the output path during rendering in order to support multi-segment timelines",
		default="")
	
	# ProRes
	autosave_video_prores: BoolProperty(
		name="Enable ProRes Output",
		description="Automatically compiles completed image sequences into a ProRes compressed .mov file",
		default=False)
	autosave_video_prores_quality: EnumProperty(
		name='ProRes Quality',
		description='Video codec used',
		items=[
			('0', 'Proxy', 'ProResProxy'),
			('1', 'LT', 'ProResLT'),
			('2', '422', 'ProRes422'),
			('3', 'HQ', 'ProRes422HQ'),
			],
		default='3')
	autosave_video_prores_location: StringProperty(
		name="Custom File Location",
		description="Set ProRes file output location and name, use single forward slash to save alongside image sequence",
		default="//../Renders/{project}",
		maxlen=4096,
		subtype="DIR_PATH")
	
	# MP4
	autosave_video_mp4: BoolProperty(
		name="Enable MP4 Output",
		description="Automatically compiles completed image sequences into an H.264 compressed .mp4 file",
		default=False)
	autosave_video_mp4_quality: IntProperty(
		name="Compression Level",
		description="CRF value where 0 is uncompressed and 51 is the lowest quality possible; 23 is the FFmpeg default but 18 produces better results (closer to visually lossless)",
		default=18,
		step=2,
		soft_min=2,
		soft_max=48,
		min=0,
		max=51)
	autosave_video_mp4_location: StringProperty(
		name="Custom File Location",
		description="Set MP4 file output location and name, use single forward slash to save alongside image sequence",
		default="//../Previews/{project}",
		maxlen=4096,
		subtype="DIR_PATH")
	
	# Custom
	autosave_video_custom: BoolProperty(
		name="Enable Custom Output",
		description="Automatically compiles completed image sequences using a custom FFmpeg string",
		default=False)
	autosave_video_custom_command: StringProperty(
		name="Custom FFmpeg Command",
		description="Custom FFmpeg command line string; {input} {fps} {output} variables must be included, but the command path is automatically prepended",
		default='{fps} {input} -vf scale=-2:1080 -c:v libx264 -preset medium -crf 18 -pix_fmt yuv420p -movflags +rtphint -movflags +faststart {output}_1080p.mp4',
				#{fps} {input} -c:v hevc_videotoolbox -pix_fmt bgra -b:v 1M -alpha_quality 1 -allow_sw 1 -vtag hvc1 {output}_alpha.mov
				#{fps} {input} -c:v hevc_videotoolbox -require_sw 1 -allow_sw 1 -alpha_quality 1.0 -vtag hvc1 {output}_alpha.mov
				#{fps} {input} -pix_fmt yuva420p {output}_alpha.webm
				#{fps} {input} -c:v libvpx -pix_fmt yuva420p -crf 16 -b:v 1M -auto-alt-ref 0 {output}_alpha.webm
		maxlen=4096)
	autosave_video_custom_location: StringProperty(
		name="Custom File Location",
		description="Set custom command file output location and name, use single forward slash to save alongside image sequence",
		default="//../Outputs/{project}",
		maxlen=4096,
		subtype="DIR_PATH")
	
	# Batch rendering options
	batch_active: BoolProperty(
		name="Batch Rendering Active",
		description="Tracks status of batch rendering",
		default=False)
	batch_type: EnumProperty(
		name='Batch Type',
		description='Choose the batch rendering system',
		items=[
			('cams', 'Cameras', 'Batch render all specified cameras'),
			('cols', 'Collections', 'Batch render all specified collections'),
			('itms', 'Items', 'Batch render all specified items'),
			(None),
			('imgs', 'Images', 'Batch render using images from specified folder'),
			],
		default='itms')
	batch_range: EnumProperty(
		name='Range',
		description='Batch render single frame or full timeline sequence',
		items=[
			('img', 'Image', 'Batch render a single frame for each element'),
			('anim', 'Animation', 'Batch render the timeline range for each element')
			],
		default='img')
	
	# Batch cameras
	# Uses the active camera for output variables
	
	# Batch collections
	batch_collection_name: StringProperty(
		name="Collection Name",
		description="Name of the collection currently being rendered (bypasses view_layer settings that aren't updated during processing)",
		default="")
	
	# Batch items
	# Uses the active item for output variables
	
	# Batch images
	batch_images_location: StringProperty(
		name="Source Folder",
		description="Source folder of images to be used in batch rendering",
		default="",
		maxlen=4096,
		subtype="DIR_PATH")
	batch_images_material: StringProperty(
		name="Target Material",
		description='Target material for batch rendering images',
		default='',
		maxlen=4096)
	batch_images_node: StringProperty(
		name="Target Node",
		description='Target node for batch rendering images',
		default='',
		maxlen=4096)
	
	# Batch index
	batch_index: IntProperty(
		name="Batch Index (set during rendering)",
		description="Dynamically populated during batch rendering with the current camera, collection, item, or image index integer starting with 0",
		default=0,
		step=1)
	batch_factor: FloatProperty(
		name="Batch Factor (set during rendering)",
		description="Dynamically populated during batch rendering with the current position (0-1) within the batch",
		default=0.25,
		min=0,
		max=1,
		subtype="FACTOR")
	batch_random: FloatProperty(
		name="Batch Random (set during rendering)",
		description="Dynamically populated during batch rendering with a random value (0-1) from the current factor hash",
		default=0.75,
		min=0,
		max=1,
		subtype="FACTOR")
	
	# Render node
	node_uvmap: StringProperty(
		name="Map",
		default="UVMap")
	node_output: StringProperty(
		name="Socket",
		default="Color")
	node_filepath: StringProperty(
		name="File Path",
		default="//{project}",
		maxlen=4096,
		subtype="DIR_PATH")
	node_filename: StringProperty(
		name="File Name",
		default="{item}-{material}-{node}-{socket}",
		maxlen=4096)
	node_overwrite: BoolProperty(
		name="Allow Overwrite",
		description="Files with the same name in the same location will be overwritten",
		default=False)
	node_colorspace: EnumProperty(
		name="UV Islands",
		items=[	('AUTO', "Auto", "Choose color space based on file format"),
				('sRGB', "sRGB", "Force sRBG color space"),
				('Non-Color', "Linear", "Force linear color space") ],
		default='AUTO')
		# ('ACES2065-1', 'ACEScg', 'AgX Base Display P3', 'AgX Base Rec.1886', 'AgX Base Rec.2020', 'AgX Base sRGB', 'AgX Log', 'Display P3', 'Filmic Log', 'Filmic sRGB', 'Khronos PBR Neutral sRGB', 'Linear CIE-XYZ D65', 'Linear CIE-XYZ E', 'Linear DCI-P3 D65', 'Linear FilmLight E-Gamut', 'Linear Rec.2020', 'Linear Rec.709', 'Non-Color', 'Rec.1886', 'Rec.2020', 'sRGB')
	node_postprocess: EnumProperty(
		name="Post Processing",
		items=[	('NONE', "None", "Leave alpha unchanged"),
				('BLEND', "Blend Fill", "Extend UV edges with blending using ImageMagick"),
				('MIP', "Mip Flood", "Extend UV edges with mip flooding using ImageMagick") ],
		default='NONE')
	node_format: EnumProperty(
		name="File Format",
		items=[	('OPEN_EXR', "EXR", ""),
				('PNG', "PNG", ""),
				('TIFF', "TIF", "") ],
		default='PNG')
	node_render_device: EnumProperty(
		name="Render Device",
		items=[	('CPU', "CPU", ""),
				('GPU', "GPU", "") ],
		default='GPU')
	node_resolution_x: IntProperty(
		name="Resolution X",
		default=4096)
	node_resolution_y: IntProperty(
		name="Resolution Y",
		default=4096)
	node_samples: IntProperty(
		name="Samples",
		default=16)
	node_margin: IntProperty(
		name="Margin",
		default=0)





###########################################################################
# Addon registration functions
# •Define classes being registered
# •Define keymap array
# •Registration function
# •Unregistration function

classes = (RenderKitPreferences, RenderKitSettings, RENDER_PT_autosave_video, RENDER_PT_autosave_image, render_proxy_start, RENDER_PT_render_region)

keymaps = []



def register():
	# Register classes
	for cls in classes:
		bpy.utils.register_class(cls)
	
	# Add extension settings reference
	bpy.types.Scene.render_kit_settings = bpy.props.PointerProperty(type=RenderKitSettings)
	
	# Update command line tool locations
	bpy.context.preferences.addons[__package__].preferences.check_magick_location()
	bpy.context.preferences.addons[__package__].preferences.check_ffmpeg_location()
	bpy.context.preferences.addons[__package__].preferences.check_voice_location()
	
	# Add proxy and batch render menu items
	bpy.types.TOPBAR_MT_render.prepend(render_proxy_menu_item)
	
	# Attach render event handlers
	bpy.app.handlers.render_init.append(render_kit_start)
	bpy.app.handlers.render_pre.append(render_kit_frame_pre)
	bpy.app.handlers.render_post.append(render_kit_frame_post)
	bpy.app.handlers.render_cancel.append(render_kit_end)
	bpy.app.handlers.render_complete.append(render_kit_end)
	
	# Add render time displays
	bpy.types.RENDER_PT_output.append(RENDER_PT_total_render_time_display)
	bpy.types.IMAGE_MT_editor_menus.append(image_viewer_feedback_display)
	
	########## Render Batch ##########
	render_batch.register()
	
	########## Render Node ##########
	render_node.register()
	
	########## Render Variables ##########
	render_variables.register()
		
	########## Render Remote ##########
	if bpy.context.preferences.addons[__package__].preferences.remote_enable:
		try:
			render_remote.register()
		except Exception as e:
			print(f"Remote render registration failed: {e}")

	
	# Add keymaps for proxy rendering
	wm = bpy.context.window_manager
	kc = wm.keyconfigs.addon
	if kc:
		km = wm.keyconfigs.addon.keymaps.new(name='Screen Editing', space_type='EMPTY')
		kmi = km.keymap_items.new(render_proxy_start.bl_idname, 'RET', 'PRESS', ctrl=True, alt=True, shift=True)
		keymaps.append((km, kmi))
	if kc:
		km = wm.keyconfigs.addon.keymaps.new(name='Screen Editing', space_type='EMPTY')
		kmi = km.keymap_items.new(render_proxy_start.bl_idname, 'RET', 'PRESS', oskey=True, alt=True, shift=True)
		keymaps.append((km, kmi))
	if kc:
		km = wm.keyconfigs.addon.keymaps.new(name='Screen Editing', space_type='EMPTY')
		kmi = km.keymap_items.new(render_proxy_start.bl_idname, 'RET', 'PRESS', ctrl=True, alt=True, shift=True)
		keymaps.append((km, kmi))
	if kc:
		km = wm.keyconfigs.addon.keymaps.new(name='Screen Editing', space_type='EMPTY')
		kmi = km.keymap_items.new(render_proxy_start.bl_idname, 'RET', 'PRESS', oskey=True, alt=True, shift=True)
		keymaps.append((km, kmi))



def unregister():
	# Remove keymaps
	for km, kmi in keymaps:
		km.keymap_items.remove(kmi)
	keymaps.clear()
		
	########## Render Remote ##########
	try:
		render_remote.unregister()
	except Exception as e:
		print(f"Remote render unregistration failed: {e}")
	
	########## Render Variables ##########
	render_variables.unregister()
	
	########## Render Node ##########
	render_node.unregister()
	
	########## Render Batch ##########
	render_batch.unregister()
	
	# Remove proxy and batch render menu items
	bpy.types.TOPBAR_MT_render.remove(render_proxy_menu_item)
	
	# Remove render event handlers
	bpy.app.handlers.render_init.remove(render_kit_start)
	bpy.app.handlers.render_pre.remove(render_kit_frame_pre)
	bpy.app.handlers.render_post.remove(render_kit_frame_post)
	bpy.app.handlers.render_cancel.remove(render_kit_end)
	bpy.app.handlers.render_complete.remove(render_kit_end)
	
	# Remove render time displays
	bpy.types.RENDER_PT_output.remove(RENDER_PT_total_render_time_display)
	bpy.types.IMAGE_MT_editor_menus.remove(image_viewer_feedback_display)
	
	# Remove extension settings reference
	del bpy.types.Scene.render_kit_settings
	
	# Deregister classes
	for cls in reversed(classes):
		bpy.utils.unregister_class(cls)



if __package__ == "__main__":
	register()