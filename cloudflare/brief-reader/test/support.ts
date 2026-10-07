import { SELF } from "cloudflare:test";

export async function browserSessionCookie(): Promise<string> {
  const response = await SELF.fetch("https://example.com/login", {
    method: "POST",
    headers: {
      "Content-Type": "application/x-www-form-urlencoded",
      Origin: "https://example.com",
    },
    body: new URLSearchParams({
      username: "admin",
      password: "test-browser-password-not-production",
    }),
    redirect: "manual",
  });
  if (response.status !== 303) throw new Error(`login failed: ${response.status}`);
  const cookie = response.headers.get("set-cookie")?.split(";", 1)[0];
  if (cookie === undefined) throw new Error("login response did not set a cookie");
  return cookie;
}
