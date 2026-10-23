import { redirect } from "next/navigation";

/** The console split into dedicated pages; keep the old URL working. */
export default function ConsoleRedirect() {
  redirect("/observability");
}
