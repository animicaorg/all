# Animica 3D Assets

Lightweight 3D artifacts used across **Website**, **Explorer**, **Wallet**, and **press**. Optimized for fast loads, PBR fidelity, and broad device support (desktop/mobile, AR Quick Look).

contrib/3d/
├─ README.md
├─ animica-orb.glb # canonical GLB (glTF 2.0, PBR)
├─ animica-orb.usdz # iOS/Quick Look export
└─ textures/
├─ orb-albedo.png # sRGB
└─ orb-normal.png # linear (OpenGL tangent space)

pgsql
Copy code

> Source of truth is **GLB**. USDZ is derived from the GLB at release time.

---

## Usage

### Web (Three.js)
```ts
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';

const loader = new GLTFLoader();
loader.load('/contrib/3d/animica-orb.glb', (gltf) => {
  const scene = gltf.scene;
  scene.traverse((o) => { if (o.isMesh) { o.frustumCulled = true; o.castShadow = false; o.receiveShadow = false; }});
  // add to your scene
});
Web (Babylon.js)
ts
Copy code
BABYLON.SceneLoader.Append('/contrib/3d/', 'animica-orb.glb', scene, () => {
  // model appended to scene
});
iOS AR Quick Look (USDZ)
html
Copy code
<a rel="ar" href="/contrib/3d/animica-orb.usdz">
  <img src="/contrib/website/og/og-default.png" alt="View in AR">
</a>
Files & conventions
Scale: meters; the orb’s diameter ≈ 0.5 m (scale in your app as needed).

Pivot: center; Y-up.

UVs: single channel, no UDIMs.

Materials: PBR Metallic-Roughness (glTF 2.0).

Textures:

orb-albedo.png → sRGB

orb-normal.png → linear, OpenGL tangent space

Triangles: target ≤ 8–12k for the orb hero; keep LODs optional.

Performance targets
animica-orb.glb ≤ 1.5 MB (after mesh compression, textures optimized)

animica-orb.usdz ≤ 3 MB

Textures ≤ 1024² for web hero; 512² for small UI

Prefer modern formats on web:

Meshopt/Draco compression for GLB (fall back if needed).

KTX2/BasisU for textures (if your viewer toolchain supports it).

Pipelines
Export from Blender → GLB
Apply transforms (Ctrl+A → All Transforms).

Set Unit Scale = 1.0, Metric, Y up.

File → Export → glTF 2.0 (.glb) with:

Include: Selected Objects (if needed), Materials = Export.

Geometry: Apply Modifiers, UVs, Normals, Tangent.

Animation: unchecked (static).

Compression: enable Draco if your runtime supports it.

Optimize GLB
Using gltfpack (mesh + texture refs):

bash
Copy code
gltfpack -i animica-orb.glb -o animica-orb.glb -cc -tc -kn -km
Using meshoptimizer (alternative):

bash
Copy code
gltf-transform optimize animica-orb.glb animica-orb.glb
Convert GLB → USDZ (macOS, Xcode tools)
bash
Copy code
xcrun usdzconverter animica-orb.glb animica-orb.usdz
Or open GLB in Reality Converter and export to USDZ.

KTX2 texture encoding (optional web path)
bash
Copy code
toktx --target_type RGBA --bcmp --t2 --genmipmap textures/orb-albedo.ktx2 textures/orb-albedo.png
toktx --normal_map    --bcmp --t2 --genmipmap textures/orb-normal.ktx2 textures/orb-normal.png
If you switch to KTX2, ensure your loader (Three.js KTX2Loader) is configured, or keep PNGs as a universal fallback.

Quality checklist
 No non-manifold geometry; normals are consistent.

 Albedo in sRGB; normal map in linear; no color management leaks.

 GLB opens in Babylon/Three/Spline/VSCode glTF viewer without warnings.

 File sizes meet targets; textures not over-res.

 USDZ previews on iOS (AR Quick Look) without material issues.

Versioning
Any visual or topology change → add an entry to contrib/CHANGELOG.md under Visuals → 3D.

Keep previous versions if removal would break release branches; mark deprecated.

License
All 3D assets © Animica. Internal reuse permitted across Animica products. Third-party sources (if ever used) must be listed in contrib/LICENSING.md.

