import bpy
import os
import math
import time
import imbuf
import numpy as np

# Internal imports
from .render_variables import replaceVariables, renderkit_variable_ui
from .utility_filecheck import checkExistingAndIncrement
from .utility_time import secondsToReadable
from . import utility_panel



###########################################################################
# Capture tuning constants

# Scrollbar/overlay strips sit at the right and bottom edges of the WINDOW
# region; trim them from every capture so they never bleed into the output.
SCROLLBAR_INSET = 18  # in UI pixels (scaled by ui_scale at runtime)

# Capture tiles overlap slightly so antialiased noodles, grid lines, and text
# never develop a sub-pixel seam at a tile boundary. The overlap is discarded
# when each tile is measured back into the final buffer.
TILE_OVERLAP = 8  # capture pixels



###########################################################################
# Node tree bounding box calculation

def compute_node_bounds(nodes, ui_scale):
	xs = []
	ys = []
	for node in nodes:
		# Frames without children collapse to a point; skip degenerate ones
		if node.bl_idname == 'NodeFrame' and node.dimensions.x < 1.0:
			continue
		loc = node.location_absolute
		# dimensions are post-display bounds (see Blender 5.2 docs); fall back to
		# the node's declared width/height when they are not yet evaluated
		w = (node.dimensions.x / ui_scale) if node.dimensions.x > 1.0 else node.width
		h = (node.dimensions.y / ui_scale) if node.dimensions.y > 1.0 else node.height
		xs.extend((loc.x, loc.x + w))
		ys.extend((loc.y - h, loc.y))
	if not xs:
		return None
	return (min(xs), min(ys), max(xs), max(ys))

def get_target_nodes(node_tree):
	# More than one node selected limits capture to the selection, otherwise capture everything
	selected = [n for n in node_tree.nodes if n.select]
	if len(selected) > 1:
		return selected
	return list(node_tree.nodes)



###########################################################################
# View helpers
# View2D has no absolute setter, but view2d.zoom_border() acts as one: it converts a region-space rectangle into View2D coordinates

def _view_corners(region):
	v2d = region.view2d
	a = v2d.region_to_view(0, 0)
	b = v2d.region_to_view(region.width, region.height)
	return a[0], b[0], a[1], b[1]  # xmin, xmax, ymin, ymax

def _view_ppu(region):
	x0, x1, y0, y1 = _view_corners(region)
	return region.width / (x1 - x0)

def _set_view_rect(window, area, region, xmin, ymin, xmax, ymax):
	v2d = region.view2d
	# Convert the desired VIEW rectangle into region coordinates understood by
	# zoom_border, using the current view as the transform (clip=False so corners
	# outside the region are preserved).
	p0 = v2d.view_to_region(xmin, ymin, clip=False)
	p1 = v2d.view_to_region(xmax, ymax, clip=False)
	with bpy.context.temp_override(window=window, area=area, region=region):
		bpy.ops.view2d.zoom_border(
			'EXEC_DEFAULT',
			xmin=min(p0[0], p1[0]), xmax=max(p0[0], p1[0]),
			ymin=min(p0[1], p1[1]), ymax=max(p0[1], p1[1]),
			wait_for_input=False, zoom_out=False)

def _set_view(window, area, region, ppu, cx, cy):
	# Set the view to a given pixels-per-unit and center. Deriving the rectangle
	# from the current region size keeps the request at the region's aspect ratio
	# (avoiding KEEPASPECT distortion); the resulting ppu is invariant to the
	# region size, so this restores the same view even if the region has resized.
	half_w = (region.width * 0.5) / ppu
	half_h = (region.height * 0.5) / ppu
	_set_view_rect(window, area, region, cx - half_w, cy - half_h, cx + half_w, cy + half_h)



###########################################################################
# Operator

class RENDERKIT_OT_render_screenshot(bpy.types.Operator):
	bl_idname = "node.render_screenshot"
	bl_label = "Render Screenshot"
	bl_options = {'REGISTER'}

	@classmethod
	def poll(cls, context):
		space = context.space_data
		return (space and space.type == 'NODE_EDITOR' and (space.edit_tree or space.node_tree))
	
	def execute(self, context):
		settings = context.scene.render_kit_settings
		self.scene = context.scene
		start_time = time.time()

		# Resolve the node editor context
		area = context.area
		if not area or area.type != 'NODE_EDITOR':
			self.report({'ERROR'}, "Render Kit — Render Screenshot must be run from a node editor")
			return {'CANCELLED'}
		space = area.spaces.active
		node_tree = space.edit_tree if space.edit_tree else space.node_tree
		if not node_tree or not node_tree.nodes:
			self.report({'ERROR'}, "Render Kit — Render Screenshot found no nodes to capture")
			return {'CANCELLED'}
		region0 = next((r for r in area.regions if r.type == 'WINDOW'), None)
		if not region0:
			self.report({'ERROR'}, "Render Kit — Render Screenshot could not find the node editor region")
			return {'CANCELLED'}

		# Relative (//) output paths need a saved .blend; fail early before capturing
		if settings.screenshot_filepath.startswith('//') and not bpy.data.filepath:
			self.report({'ERROR'}, "Render Kit — Render Screenshot: save the .blend first, or set an absolute output path (relative // paths need a saved file)")
			return {'CANCELLED'}

		ui_scale = context.preferences.system.ui_scale
		requested_ppu = ui_scale * settings.screenshot_scale

		# Bounding box of the target nodes (view units; padding is applied later)
		target_nodes = get_target_nodes(node_tree)
		raw_bounds = compute_node_bounds(target_nodes, ui_scale)
		if raw_bounds is None:
			self.report({'ERROR'}, "Render Kit — Render Screenshot could not measure the node bounds")
			return {'CANCELLED'}

		# Snapshot the settings we need for the output write
		padding = settings.screenshot_padding
		self.file_format = settings.screenshot_format
		self.quality = settings.screenshot_quality
		self.filepath = settings.screenshot_filepath
		self.overwrite = settings.screenshot_overwrite

		window = context.window

		# Save the exact view (zoom + center) and overlay so they can be restored after
		x0, x1, y0, y1 = _view_corners(region0)
		orig_ppu = _view_ppu(region0)
		orig_cx = (x0 + x1) * 0.5
		orig_cy = (y0 + y1) * 0.5
		orig_show_path = getattr(space.overlay, "show_context_path", None)

		# Fullscreen/focus mode gives each tile far more framebuffer pixels at the same
		# node-space scale: fewer tiles, fewer seams, often a single capture. Only enter
		# it if the user was not already in a maximized/fullscreen area, so cleanup never
		# toggles a state the user set up themselves. show_fullscreen is True for both
		# Focus Mode (SCREENFULL) and Maximize Area (SCREENMAXIMIZED).
		#
		# The capture runs synchronously (not via a modal timer): a modal operator cannot
		# reliably force the window to repaint between steps — every tile then reads the
		# same stale framebuffer — and it cannot tear down its own fullscreen screen on
		# exit. Doing it inline lets us drive DRAW_WIN_SWAP and the fullscreen toggle
		# directly, which both work outside the modal handler.
		entered_fullscreen = not context.screen.show_fullscreen
		if entered_fullscreen:
			with context.temp_override(window=window, area=area):
				bpy.ops.screen.screen_full_area(use_hide_panels=True)

		out_w = out_h = 0
		canvas = None
		capture_error = None
		try:
			# Reacquire the (now fullscreen) Node Editor region: the fullscreen transition
			# resized the WINDOW region, so the earlier measurement is stale
			screen = window.screen
			area = max(
				(a for a in screen.areas if a.type == 'NODE_EDITOR'),
				key=lambda a: a.width * a.height, default=None)
			region = next((r for r in area.regions if r.type == 'WINDOW'), None) if area else None
			if not area or not region:
				raise RuntimeError("lost the node editor during capture")

			# Hide the breadcrumb/context-path overlay so it cannot occlude nodes
			if orig_show_path is not None:
				space.overlay.show_context_path = False

			# Calibrate: request the target zoom and read back what Blender allowed. The
			# node editor clamps to a hard ~2.31 ppu ceiling, so measuring the actual
			# value discovers the real capture density without probing.
			bcx = (raw_bounds[0] + raw_bounds[2]) * 0.5
			bcy = (raw_bounds[1] + raw_bounds[3]) * 0.5
			_set_view(window, area, region, requested_ppu, bcx, bcy)
			ppu = _view_ppu(region)
			if ppu <= 0:
				raise RuntimeError("could not calibrate the view")

			# Padded output rectangle in view units, and its pixel resolution
			pad_view = padding / ppu
			bxmin = raw_bounds[0] - pad_view
			bymin = raw_bounds[1] - pad_view
			bxmax = raw_bounds[2] + pad_view
			bymax = raw_bounds[3] + pad_view
			out_w = max(1, int(round((bxmax - bxmin) * ppu)))
			out_h = max(1, int(round((bymax - bymin) * ppu)))

			# Usable capture rect within the region (trim the scrollbar strips)
			inset = max(0, int(round(SCROLLBAR_INSET * ui_scale)))
			cap_w = max(1, region.width - inset)

			# Tile the output, stepping by slightly less than a full capture so tiles overlap
			step_x = max(1, cap_w - TILE_OVERLAP)
			step_y = max(1, (region.height - inset) - TILE_OVERLAP)
			tiles_x = max(1, math.ceil(out_w / step_x))
			tiles_y = max(1, math.ceil(out_h / step_y))

			# Final image accumulator (RGBA, row 0 = bottom, matching both
			# Window.screenshot and ImBuf buffer ordering for a direct copy)
			canvas = np.zeros((out_h, out_w, 4), dtype=np.uint8)

			for ty in range(tiles_y):
				for tx in range(tiles_x):
					# Aim the view so this tile's bottom-left lands at the capture rect
					want_left = bxmin + (tx * step_x) / ppu
					want_bottom = bymin + (ty * step_y) / ppu
					view_xmin = want_left
					view_ymin = want_bottom - inset / ppu
					view_xmax = view_xmin + region.width / ppu
					view_ymax = view_ymin + region.height / ppu
					_set_view_rect(window, area, region, view_xmin, view_ymin, view_xmax, view_ymax)

					# Force a synchronous draw + buffer swap so the on-screen framebuffer
					# reflects THIS tile's view before we read it. Only a real DRAW_WIN_SWAP
					# repaints deterministically; tagging a redraw does not.
					with context.temp_override(window=window, area=area, region=region):
						bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)

					# Measure where the capture rect actually landed after clamping, then read
					# the framebuffer for exactly that rect (scrollbar strips excluded)
					cvx0, cvy0 = region.view2d.region_to_view(0, inset)
					rx0 = region.x
					ry0 = region.y + inset
					rx1 = region.x + cap_w
					ry1 = region.y + region.height
					tile = np.asarray(window.screenshot(region=((rx0, ry0), (rx1, ry1)), use_alpha=False))
					th, tw = tile.shape[0], tile.shape[1]

					# Place the tile by its measured view position; ppu matches the canvas so
					# the copy is a pure integer offset, and overlap/overshoot is clipped
					off_c = int(round((cvx0 - bxmin) * ppu))
					off_r = int(round((cvy0 - bymin) * ppu))
					dst_r0 = max(0, off_r)
					dst_c0 = max(0, off_c)
					dst_r1 = min(out_h, off_r + th)
					dst_c1 = min(out_w, off_c + tw)
					if dst_r1 > dst_r0 and dst_c1 > dst_c0:
						src_r0 = dst_r0 - off_r
						src_c0 = dst_c0 - off_c
						canvas[dst_r0:dst_r1, dst_c0:dst_c1] = tile[src_r0:src_r0 + (dst_r1 - dst_r0), src_c0:src_c0 + (dst_c1 - dst_c0)]
		except Exception as exc:
			capture_error = str(exc)
			print(str(exc) + " | Error in Render Kit: screenshot capture failed")
		finally:
			self._restore_ui(context, window, space, orig_show_path, entered_fullscreen, orig_ppu, orig_cx, orig_cy)

		if capture_error is not None or canvas is None:
			self.report({'ERROR'}, "Render Kit — Render Screenshot failed during capture (see console)")
			return {'CANCELLED'}

		ok, file_path, message = self._write_output(canvas, out_w, out_h)
		if not ok:
			self.report({'ERROR'}, message)
			return {'CANCELLED'}

		render_time = round(time.time() - start_time, 2)
		self.report({'INFO'}, f"Screenshot saved to {file_path}")
		self.show_completion_popup(context, file_path, out_w, out_h, render_time)
		return {'FINISHED'}

	def _restore_ui(self, context, window, space, orig_show_path, entered_fullscreen, orig_ppu, orig_cx, orig_cy):
		# Undo overlay, workspace, and view changes; safe regardless of how far the
		# capture progressed. Runs in a finally block so a mid-capture error still cleans up.
		if orig_show_path is not None and space:
			try:
				space.overlay.show_context_path = orig_show_path
			except Exception:
				pass

		# Exit with the SAME use_hide_panels the entry used. Focus Mode is entered with
		# use_hide_panels=True; toggling back with the default (False) makes the operator
		# cancel — it only reverts a matching state — which is what stranded the editor in
		# fullscreen. show_fullscreen is True for the nonnormal (fullscreen) screen.
		if entered_fullscreen and window.screen.show_fullscreen:
			area = next((a for a in window.screen.areas if a.type == 'NODE_EDITOR'), None)
			if area:
				with context.temp_override(window=window, area=area):
					bpy.ops.screen.screen_full_area(use_hide_panels=True)

		# Restore the user's original view in the (now non-fullscreen) editor
		screen = window.screen
		area = next((a for a in screen.areas if a.type == 'NODE_EDITOR'), None)
		region = next((r for r in area.regions if r.type == 'WINDOW'), None) if area else None
		if area and region:
			_set_view(window, area, region, orig_ppu, orig_cx, orig_cy)

	def _write_output(self, canvas, out_w, out_h):
		# Resolve the output path
		ext = {'PNG': 'png', 'JPEG': 'jpg', 'TIFF': 'tif'}[self.file_format]
		file_path = self.filepath + '.' + ext
		file_path = replaceVariables(self.scene, file_path)
		file_path = checkExistingAndIncrement(file_path, overwrite=self.overwrite)
		absolute_path = bpy.path.abspath(file_path)
		out_dir = os.path.dirname(absolute_path)
		if not out_dir:
			return False, file_path, f"Render Kit — Render Screenshot could not resolve a valid output path ('{file_path}')"
		try:
			os.makedirs(out_dir, exist_ok=True)
		except OSError as exc:
			print(str(exc) + " | Error in Render Kit: failed to create screenshot output folder")
			return False, file_path, f"Render Kit — Render Screenshot could not create the output folder '{out_dir}'"
		if not os.access(out_dir, os.W_OK):
			return False, file_path, f"Render Kit — Render Screenshot output folder is not writable: '{out_dir}'"
		
		# Compose the captured tiles into a native ImBuf and write it directly —
		# no temporary files and no external tools
		try:
			buf = imbuf.new((out_w, out_h), planes=32, buffer_type='BYTE')
			with buf.with_buffer(write=True) as view:
				np.asarray(view)[:] = canvas
			buf.file_type = self.file_format
			if self.file_format == 'JPEG':
				buf.quality = self.quality
			elif self.file_format == 'PNG':
				buf.compress = self.quality
			imbuf.write(buf, filepath=absolute_path)
		except Exception as exc:
			print(str(exc) + " | Error in Render Kit: failed to write screenshot image")
			return False, file_path, "Render Kit — Render Screenshot failed to write the image (see console)"
		
		return True, file_path, ""
	
	def show_completion_popup(self, context, filepath, out_w, out_h, render_time):
		def draw(self, context):
			self.layout.label(text=filepath)
			self.layout.label(text=f"{out_w} × {out_h} px")
		context.window_manager.popup_menu(draw, title="Render Screenshot Completed " + secondsToReadable(render_time), icon='IMAGE_DATA')



###########################################################################
# UI rendering classes

class RENDERKIT_PT_render_screenshot(bpy.types.Panel):
	bl_label = "Render Screenshot"
	bl_space_type = 'NODE_EDITOR'
	bl_region_type = 'UI'
	bl_category = 'Node'
	bl_order = 41
	bl_options = {'DEFAULT_CLOSED'}
	category_preference = "renderscreenshot_category"
	
	@classmethod
	def poll(cls, context):
		prefs = context.preferences.addons[__package__].preferences
		space = context.space_data
		return (prefs.renderscreenshot_enable and space and space.type == 'NODE_EDITOR' and (space.edit_tree or space.node_tree))
	
	def draw(self, context):
		settings = context.scene.render_kit_settings
		space = context.space_data
		layout = self.layout
		
		node_tree = space.edit_tree if space.edit_tree else space.node_tree
		
		# Predicted output resolution (actual may be lower where Blender clamps the zoom)
		ui_scale = context.preferences.system.ui_scale
		ppu = ui_scale * settings.screenshot_scale
		target_nodes = get_target_nodes(node_tree) if node_tree else []
		bounds = compute_node_bounds(target_nodes, ui_scale) if target_nodes else None
		if bounds:
			pad_view = settings.screenshot_padding / ppu
			w = int(round((bounds[2] - bounds[0] + 2 * pad_view) * ppu))
			h = int(round((bounds[3] - bounds[1] + 2 * pad_view) * ppu))
			scope = "selection" if len([n for n in node_tree.nodes if n.select]) > 1 else "full tree"
			operator_text = f"Save Image  ({w}×{h}px)"
#			layout.label(text=f"Estimated: {w}×{h}px ({scope})", icon='IMAGE_DATA')
		
		# Render screenshot button
#		layout.operator(RENDERKIT_OT_render_screenshot.bl_idname, icon='IMAGE_DATA')
		layout.operator(RENDERKIT_OT_render_screenshot.bl_idname, text=operator_text, icon='IMAGE_PLANE') # IMAGE_DATA IMAGE_BACKGROUND IMAGE_PLANE FILE_IMAGE
		
		# Additional settings
		header, panel = layout.panel("render_screenshot_subpanel", default_closed=True)
		header.label(text="Settings")
		if panel:
			# Settings
			grid = panel.grid_flow(row_major=True, columns=2, even_columns=True, even_rows=True, align=True)
			grid.prop(settings, "screenshot_scale")
			grid.prop(settings, "screenshot_padding")
			panel.separator()
			
			# Output filepath and variable UI
			renderkit_variable_ui(panel, context, paths=settings.screenshot_filepath, postrender=False, noderender=False, autoclose=True)
			panel.prop(settings, "screenshot_filepath", text='')
			panel.separator()
			
			# Format
			col = panel.column(align=True)
			row = col.row()
			row.prop(settings, "screenshot_format", expand=True)
			if settings.screenshot_format in {'PNG', 'JPEG'}:
				col.prop(settings, "screenshot_quality")
			
			# Overwrite
			panel.prop(settings, "screenshot_overwrite")



# Class Registration

classes = [RENDERKIT_OT_render_screenshot]

# Registered from the tab category set in the extension preferences
panels = [RENDERKIT_PT_render_screenshot]

def register():
	# Register classes
	for cls in classes:
		bpy.utils.register_class(cls)
	
	# Register panels
	utility_panel.register_panels(panels)

def unregister():
	# Deregister panels
	utility_panel.unregister_panels(panels)
	
	# Deregister classes
	for cls in reversed(classes):
		bpy.utils.unregister_class(cls)

if __name__ == "__main__":
	register()
	