declare module "*.css" {
  const text: string;
  export default text;
}

declare module "*/portal.js" {
  const text: string;
  export default text;
}

declare module "*.ttf" {
  const data: ArrayBuffer;
  export default data;
}
