import { redirect } from "next/navigation";

export default function RootPage() {
  // Galaxy is the default tab (roadmap Section 5's locked-in layout).
  redirect("/galaxy");
}
