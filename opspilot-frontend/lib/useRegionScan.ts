"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { getRegions, scanRegion, ScanCooldownError, type ScanResponse } from "@/lib/api";

// Shared "region picker + region-wide scan" state/lifecycle, lifted out of
// components/GalaxyView.tsx rather than duplicated a second and third time.
// Idle Resources (components/IdleResourcesPanel.tsx) and Cost Overview
// (components/CostOverviewPanel.tsx) both need exactly this slice --
// region list, cache-or-scan on mount/region switch, a debounced manual
// refresh, the stale-cache-warning/hard-error split, and a live "last
// updated Nm ago" readout -- with none of GalaxyView's galaxy/cluster-
// specific layout state, so extracting a hook is a clean lift rather than
// a big refactor of GalaxyView itself (which stays untouched here). Every
// comment below mirrors GalaxyView's own reasoning for the same lines --
// see that file if a "why" seems terse here.
const REFRESH_COOLDOWN_SECONDS = 45;

export interface UseRegionScanResult {
  regions: string[];
  region: string;
  setRegion: (region: string) => void;
  regionsError: string | null;
  scan: ScanResponse | null;
  hardError: string | null;
  warning: string | null;
  dismissWarning: () => void;
  loading: boolean;
  refreshing: boolean;
  cooldown: number;
  refresh: () => void;
  // Bumps every 30s so a "last updated Nm ago" readout computed from
  // `scan.last_updated` stays fresh without a real refetch (GalaxyView's
  // own `forceTick` pattern).
  tick: number;
}

export function useRegionScan(): UseRegionScanResult {
  const [regions, setRegions] = useState<string[]>([]);
  const [region, setRegionState] = useState("us-east-1");
  const [regionsError, setRegionsError] = useState<string | null>(null);

  const [scan, setScan] = useState<ScanResponse | null>(null);
  const [hardError, setHardError] = useState<string | null>(null);
  const [warning, setWarning] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [cooldown, setCooldown] = useState(0);
  const [tick, setTick] = useState(0);

  const scanRef = useRef<ScanResponse | null>(null);
  useEffect(() => {
    scanRef.current = scan;
  }, [scan]);

  // Out-of-order response guard (identical reasoning to GalaxyView's own
  // requestIdRef -- see that file's comment): only the most recently
  // issued request is allowed to apply its result to state.
  const requestIdRef = useRef(0);

  // In-flight request de-dupe (identical reasoning to GalaxyView's own
  // inFlightRef, added for the same StrictMode-double-invoke bug this hook
  // must not reintroduce): the second caller for the exact same
  // `${targetRegion}:${force}` target reuses the first caller's still-
  // pending promise instead of firing a second real network request.
  const inFlightRef = useRef<Map<string, Promise<ScanResponse>>>(new Map());

  useEffect(() => {
    const id = setInterval(() => setTick((t) => t + 1), 30_000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    if (cooldown <= 0) return;
    const id = setTimeout(() => setCooldown((c) => Math.max(0, c - 1)), 1000);
    return () => clearTimeout(id);
  }, [cooldown]);

  useEffect(() => {
    (async () => {
      try {
        const res = await getRegions();
        setRegions(res.regions);
        if (res.regions.length > 0) {
          setRegionState((prev) => (res.regions.includes(prev) ? prev : res.regions[0]));
        }
      } catch (err) {
        setRegionsError(err instanceof Error ? err.message : "Couldn't load the region list.");
      }
    })();
  }, []);

  const runScan = useCallback(async (targetRegion: string, force: boolean) => {
    const myRequestId = ++requestIdRef.current;

    if (force) setRefreshing(true);
    else setLoading(true);

    const inFlightKey = `${targetRegion}:${force}`;
    let scanPromise = inFlightRef.current.get(inFlightKey);
    if (!scanPromise) {
      scanPromise = scanRegion(targetRegion, force);
      inFlightRef.current.set(inFlightKey, scanPromise);
      scanPromise.catch(() => {}).finally(() => {
        if (inFlightRef.current.get(inFlightKey) === scanPromise) {
          inFlightRef.current.delete(inFlightKey);
        }
      });
    }

    try {
      const res = await scanPromise;
      if (requestIdRef.current !== myRequestId) return;
      setScan(res);
      setHardError(null);
      // Non-null `.error` = stale cache served after a failed rescan
      // (roadmap 3.4) -- surface as a warning, keep showing res.resources.
      setWarning(res.error);
      if (force) setCooldown(REFRESH_COOLDOWN_SECONDS);
    } catch (err) {
      if (requestIdRef.current !== myRequestId) return;
      if (err instanceof ScanCooldownError) {
        if (err.cached) {
          setScan(err.cached);
          setHardError(null);
        }
        setWarning(`Refresh is cooling down -- try again in ${err.retryAfterSeconds}s.`);
        setCooldown(err.retryAfterSeconds);
      } else {
        const msg = err instanceof Error ? err.message : "Unknown error";
        if (scanRef.current) {
          // Never blank the dashboard (roadmap 3.4) -- keep whatever was
          // last shown and surface a non-blocking warning instead.
          setWarning(`Couldn't load ${targetRegion}: ${msg} -- showing last available data.`);
        } else {
          setHardError(msg);
        }
      }
    } finally {
      if (requestIdRef.current === myRequestId) {
        setLoading(false);
        setRefreshing(false);
      }
    }
  }, []);

  // Initial load + every region switch: cheap cache-or-scan (force=false).
  useEffect(() => {
    runScan(region, false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [region]);

  const setRegion = useCallback((r: string) => setRegionState(r), []);
  const dismissWarning = useCallback(() => setWarning(null), []);
  const refresh = useCallback(() => {
    if (refreshing || cooldown > 0) return;
    runScan(region, true);
  }, [refreshing, cooldown, region, runScan]);

  return {
    regions,
    region,
    setRegion,
    regionsError,
    scan,
    hardError,
    warning,
    dismissWarning,
    loading,
    refreshing,
    cooldown,
    refresh,
    tick,
  };
}
