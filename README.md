# Launch Render Kit — Blender Rendering Management

![3D render of an abstract R-shaped logo made up of blocks with some rounded corners in soft reds and oranges, text in the image reads Render Kit from the Mograph team at Launch by NTT DATA](images/RenderKit.jpg)

## Known Issue Notice:

We're running into some sporadic and unpredictable issues with sequence rendering in Blender 4.5.5 and 5.0. Hundreds or thousands of frames will render just fine, then suddenly `ERROR: Python context internal state bug. this should not happen!` will start spamming the terminal output, or a fatal crash will occur with `Blender(37970,0x345fff000) malloc: *** error **for** object 0xc1e791400: pointer being freed was **not** allocated` or similar.

From everything tested so far, it does appear to be tied to this extension and usage of render event handlers, but it's possible it's not the python itself, instead something deeper in the Blender rendering system. Refactoring some the code has not helped so far.

This is an ongoing challenge, and will not be solved by the end of 2025. Planning to get back to it in the new year!

## Features:

- ### Render Variables

  - Adds dynamic variables to rendering output paths, including:

    - The name of the current project, scene, view layer, collection, camera, selected item, material, node, socket (for node rendering only), or closest timeline marker

    - The selected render engine, device, samples, features, and rendering duration (in total seconds or HH:MM:SS formats)

    - The current computer host, processor, platform, system type, OS version, Python version, and Blender version

    - Date, time, global serial number, current frame, and batch rendering index (see below batch feature)

      ![Screenshot-VariableList](images/Screenshot-VariableList.png)

    - Custom scene, render layer, and object data values that can be set using drivers or animation data

      ![Screenshot-VariableData](images/Screenshot-VariableData.png)

- ### Autosave Images

  - Automatically saves every render in a specified folder using custom name and formatting

    ![Screenshot-AutosaveImages](images/Screenshot-AutosaveImages.png)

- ### Autosave Videos

  - Automatically processes image sequences using FFmpeg after rendering completes

    ![Screenshot-AutosaveVideos](images/Screenshot-AutosaveVideos.png)

- ### Render Batch

  - One-click rendering of collections, items, cameras, or texture folders to individual images or sequences

    ![Screenshot-Batch](images/Screenshot-Batch.png)

- ### Render Node

  - One-click baking of material nodes to texture files

    ![Screenshot-Node](images/Screenshot-Node.png)

- ### Render Proxy

  - Shortcut for triggering a proxy render with resolution and engine settings

    ![Screenshot-Proxy](images/Screenshot-Proxy.png)

- ### Render Region

  - Adds numerical inputs for the render region feature

    ![Screenshot-Region](images/Screenshot-Region.png)

- ### Render Data

  - Tracks the total time spent rendering a project and displays estimated time remaining during animation sequence rendering

- ### Render Notifications

  - Sends an email, push notification, or announces render statistics at the completion of renders over a given time limit



## Installation via Extensions Platform:

- Go to Blender Preferences > Get Extensions > Repositories > **＋** > Add Remote Repository
- Set the URL to `https://jeinselen.github.io/Launch-Blender-Extensions/index.json`
- Enable `Check for Updates on Start`
- Filter the available extensions for "Launch" and install as needed



## Installation via Drag-and-Drop:

- Click and drag one of the file links from the [repository list page](https://jeinselen.github.io/Launch-Blender-Extensions/) into Blender



## Installation via Download:

- Download the .zip file for a specific kit
- Drag-and-drop the file into Blender



These latter two methods will not connect to the centralised repository here on GitHub and updates will not be automatically available. If you don't need easy updates, don't want GitHub servers to be pinged when you start up Blender, or would just like to try some extensions without adding yet another repository to your Blender settings, this is the option for you.



## Notes:

Software is provided as-is with no warranty or provision of suitability. These are internal tools and are shared because we want to support an open community. Bug reports are welcomed, but we cannot commit to fixing or adding features. Not all features may be actively maintained, as they're updated on an as-needed basis.
