# E-paper product marketing assets

## Core rule

The device screen in commercial/product images must use actual 800x480 horizontal screenshots from this project. Do not ask an image model to invent, redraw, or reinterpret the screen UI.

Use img-2 only for the outer scene, hardware/environment mockup, or blank device frame. Composite the real screen capture afterward.

## Folders

- `source_screens/`: copied 800x480 source screenshots.
- `mockups/`: generated promotional drafts that place those screenshots inside a device frame.
- `img2_bases/`: img-2 generated Taobao-style bases with blank green screen placeholders.
- `taobao_img2/`: final Taobao-style promotional images composited from img-2 bases plus real screenshots and exact scripted Chinese copy.

## Regenerate

```powershell
python tools\create_marketing_assets.py
python tools\composite_taobao_img2_assets.py
```

The scripts validate every source screenshot is exactly `800x480` before writing mockups. `composite_taobao_img2_assets.py` also detects the green placeholders in img-2 bases and replaces them with real screenshots.

## Taobao-style workflow

1. Use img-2 to generate only the mainland Taobao-style ecommerce scene, device frame, packaging, and green screen placeholder.
2. Keep the screen placeholder flat chroma green so it can be detected.
3. Composite real 800x480 screenshots into the screen area with `tools\composite_taobao_img2_assets.py`.
4. Add all Chinese titles, selling points, and parameter text through code, not through img-2.

## Suggested img-2 prompt constraint

```text
Create only the product environment and a blank 7.5-inch landscape e-paper device frame.
The screen area must be a plain flat empty rectangle with the same 5:3 aspect ratio as 800x480.
Do not draw UI, widgets, text, icons, weather, calendar, news, charts, or any screen content.
Leave the screen blank for later compositing with a real screenshot.
No physical buttons on the device front.
Use a mainland China Taobao electronics listing style if this is for product promotion.
```
