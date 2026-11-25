// Files bundled into the Worker: fonts as bytes, the image renderer's WebAssembly as a compiled module.
declare module "*.ttf" { const data: ArrayBuffer; export default data; }
declare module "*.wasm" { const mod: WebAssembly.Module; export default mod; }
