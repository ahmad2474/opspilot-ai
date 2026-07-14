import GalaxyView from "@/components/GalaxyView";

// Deliberately no mx-auto max-w-6xl wrapper -- and no padding at all -- here,
// unlike every other page: the galaxy starfield canvas (GalaxyView's own
// internal h-[calc(100vh-5rem)] w-full container) needs to be truly
// full-bleed edge-to-edge below NavBar (roadmap Section 5 layout, prototype
// parity with docs/aws-galaxy-dashboard.jsx line 95). See app/layout.tsx's
// <main> for the other half of this.
export default function GalaxyPage() {
  return <GalaxyView />;
}
