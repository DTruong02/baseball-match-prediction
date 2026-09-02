"use client";

import { useEffect, useState } from "react";

import { AppShell } from "@/components/AppShell";
import { ProtectedRoute } from "@/components/ProtectedRoute";
import { ApiError, fetchModelPerformance } from "@/lib/api";
import type { CalibrationBucket, ModelPerformance } from "@/lib/types";

function formatMetric(value: number | null, decimals = 3): string {
  if (value == null) {
    return "—";
  }
  return value.toFixed(decimals);
}

function formatPercent(value: number | null): string {
  if (value == null) {
    return "—";
  }
  return `${(value * 100).toFixed(1)}%`;
}

function formatBand(bucket: CalibrationBucket): string {
  const low = (bucket.bin_low * 100).toFixed(0);
  const high = (bucket.bin_high * 100).toFixed(0);
  return `${low}–${high}%`;
}

export default function ModelPerformancePage() {
  const currentYear = new Date().getFullYear();
  const [season, setSeason] = useState(String(currentYear));
  const [team, setTeam] = useState("");
  const [confidenceBand, setConfidenceBand] = useState("");
  const [performance, setPerformance] = useState<ModelPerformance | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function loadPerformance() {
      setLoading(true);
      setError(null);

      const params: {
        season?: number;
        team?: string;
        confidence_band?: string;
      } = {};

      const parsedSeason = Number(season);
      if (season && Number.isFinite(parsedSeason)) {
        params.season = parsedSeason;
      }
      if (team.trim()) {
        params.team = team.trim().toUpperCase();
      }
      if (confidenceBand.trim()) {
        params.confidence_band = confidenceBand.trim();
      }

      try {
        const data = await fetchModelPerformance(params);
        if (!cancelled) {
          setPerformance(data);
        }
      } catch (err) {
        if (!cancelled) {
          if (err instanceof ApiError) {
            setError(err.message);
          } else {
            setError("Failed to load model performance.");
          }
          setPerformance(null);
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    void loadPerformance();

    return () => {
      cancelled = true;
    };
  }, [season, team, confidenceBand]);

  return (
    <ProtectedRoute>
      <AppShell>
        <section className="space-y-6">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">
              Model performance
            </h1>
            <p className="mt-1 text-sm text-muted">
              Production metrics for scored pregame predictions.
            </p>
          </div>

          <div className="rounded-xl border border-border bg-surface p-6">
            <h2 className="text-sm font-medium text-muted">Filters</h2>
            <div className="mt-4 grid gap-4 sm:grid-cols-3">
              <label className="flex flex-col gap-1.5 text-sm">
                <span className="text-muted">Season</span>
                <input
                  type="number"
                  min={2000}
                  max={2100}
                  value={season}
                  onChange={(event) => setSeason(event.target.value)}
                  className="rounded-lg border border-border bg-surface px-3 py-2 outline-none ring-accent/40 transition focus:border-accent focus:ring-2"
                />
              </label>
              <label className="flex flex-col gap-1.5 text-sm">
                <span className="text-muted">Team (abbrev)</span>
                <input
                  type="text"
                  placeholder="e.g. BOS"
                  value={team}
                  onChange={(event) => setTeam(event.target.value)}
                  className="rounded-lg border border-border bg-surface px-3 py-2 uppercase outline-none ring-accent/40 transition focus:border-accent focus:ring-2"
                />
              </label>
              <label className="flex flex-col gap-1.5 text-sm">
                <span className="text-muted">Confidence band</span>
                <input
                  type="text"
                  placeholder="e.g. 0.55-0.65"
                  value={confidenceBand}
                  onChange={(event) => setConfidenceBand(event.target.value)}
                  className="rounded-lg border border-border bg-surface px-3 py-2 outline-none ring-accent/40 transition focus:border-accent focus:ring-2"
                />
              </label>
            </div>
          </div>

          {loading ? (
            <p className="text-sm text-muted">Loading performance…</p>
          ) : error ? (
            <p className="rounded-lg border border-danger/30 bg-danger/10 px-4 py-3 text-sm text-danger">
              {error}
            </p>
          ) : performance ? (
            <>
              <div className="rounded-xl border border-border bg-surface p-6">
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <h2 className="text-sm font-medium text-muted">Summary</h2>
                  <p className="text-xs text-muted">
                    {performance.run_id} · {performance.n_games} games scored
                  </p>
                </div>
                <dl className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                  <MetricCard label="Accuracy" value={formatPercent(performance.accuracy)} />
                  <MetricCard label="ROC-AUC" value={formatMetric(performance.roc_auc)} />
                  <MetricCard label="Log loss" value={formatMetric(performance.log_loss)} />
                  <MetricCard label="Brier score" value={formatMetric(performance.brier)} />
                </dl>
              </div>

              <div className="rounded-xl border border-border bg-surface p-6">
                <h2 className="text-sm font-medium text-muted">Calibration</h2>
                {performance.calibration_buckets.length === 0 ? (
                  <p className="mt-3 text-sm text-muted">
                    No calibration data for the current filters.
                  </p>
                ) : (
                  <div className="mt-4 overflow-x-auto">
                    <table className="w-full min-w-[32rem] text-left text-sm">
                      <thead>
                        <tr className="border-b border-border text-xs uppercase tracking-wide text-muted">
                          <th className="pb-3 pr-4 font-medium">Confidence</th>
                          <th className="pb-3 pr-4 font-medium">Games</th>
                          <th className="pb-3 pr-4 font-medium">Predicted</th>
                          <th className="pb-3 pr-4 font-medium">Actual</th>
                          <th className="pb-3 font-medium">Calibration</th>
                        </tr>
                      </thead>
                      <tbody>
                        {performance.calibration_buckets.map((bucket) => (
                          <CalibrationRow key={`${bucket.bin_low}-${bucket.bin_high}`} bucket={bucket} />
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            </>
          ) : null}
        </section>
      </AppShell>
    </ProtectedRoute>
  );
}

function MetricCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-border bg-surface-elevated px-4 py-3">
      <dt className="text-xs uppercase tracking-wide text-muted">{label}</dt>
      <dd className="mt-1 font-mono text-xl tabular-nums">{value}</dd>
    </div>
  );
}

function CalibrationRow({ bucket }: { bucket: CalibrationBucket }) {
  const predicted = bucket.predicted_mean ?? 0;
  const actual = bucket.actual_rate ?? 0;
  const gap = Math.abs(predicted - actual);
  const wellCalibrated = gap <= 0.05;

  return (
    <tr className="border-b border-border/60 last:border-0">
      <td className="py-3 pr-4 font-mono tabular-nums">{formatBand(bucket)}</td>
      <td className="py-3 pr-4 font-mono tabular-nums">{bucket.n}</td>
      <td className="py-3 pr-4 font-mono tabular-nums">
        {formatPercent(bucket.predicted_mean)}
      </td>
      <td className="py-3 pr-4 font-mono tabular-nums">
        {formatPercent(bucket.actual_rate)}
      </td>
      <td className="py-3">
        <div className="flex items-center gap-3">
          <div className="relative h-2 w-28 overflow-hidden rounded-full bg-surface-elevated">
            <div
              className="absolute inset-y-0 left-0 rounded-full bg-muted/50"
              style={{ width: `${Math.round(predicted * 100)}%` }}
            />
            <div
              className={`absolute inset-y-0 left-0 rounded-full ${
                wellCalibrated ? "bg-accent/70" : "bg-danger/70"
              }`}
              style={{ width: `${Math.round(actual * 100)}%` }}
            />
          </div>
          <span className="text-xs text-muted">
            Δ {formatPercent(gap)}
          </span>
        </div>
      </td>
    </tr>
  );
}
