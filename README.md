# Launch Render Kit — Blender Rendering Management

![3D render of an abstract R-shaped logo made up of blocks with some rounded corners in soft reds and oranges, text in the image reads Render Kit from the Mograph team at Launch by NTT DATA](images/RenderKit.jpg)

Combining multiple utilities like render variables, video compression, autosaving, proxy, and render region controls for Blender 2.8-4.1, Mesh Kit refactors the code for Blender 4.2. Features haven't changed significantly, so for the time being please refer to the original documentation for details.

Includes:

- https://github.com/jeinselen/VF-BlenderAutosaveRender
	- Output variables
	- Autosave videos via FFmpeg
	- Autosave images
	- Render time estimation and logging
	- Batch rendering
	- Rendering complete notifications
- https://github.com/jeinselen/VF-BlenderRenderProxyAnimation
	- Proxy rendering
- https://github.com/jeinselen/VF-BlenderNumericalRenderRegion
	- Numerical render region inputs



***WARNING: This extension is in early beta and should be installed only for testing purposes.***



## Installation via Extensions Platform:

- Go to Blender Preferences > Get Extensions > Repositories > **＋** > Add Remote Repository
- Set the URL to `https://jeinselen.github.io/Launch-Blender-Extensions/index.json`
- Set the local directory if desired (relative paths seem to fail, try absolute instead)
- Enable `Check for Updates on Start`
- Filter the available extensions for "Launch" and install as needed



## Installation via Download:

- Download the .zip file for a specific kit
- Drag-and-drop the file into Blender

This method will not connect to the centralised repository here on GitHub and updates will not be automatically available. If you don't need easy updates, don't want GitHub servers to be pinged when you start up Blender, or would just like to try some extensions without adding yet another repository to your Blender settings, this is the option for you.

Software is provided as-is with no warranty or provision of suitability. These are internal tools and are shared because we want to support an open community. Bug reports are welcomed, but we cannot commit to fixing or adding features.
