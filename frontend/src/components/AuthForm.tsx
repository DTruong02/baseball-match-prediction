"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";

import { ApiError } from "@/lib/api";

interface AuthFormProps {
  mode: "login" | "register";
  onSubmit: (email: string, password: string) => Promise<void>;
}

export function AuthForm({ mode, onSubmit }: AuthFormProps) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const isRegister = mode === "register";

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);

    try {
      await onSubmit(email.trim(), password);
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setError("Something went wrong. Please try again.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="mx-auto w-full max-w-md">
      <div className="rounded-2xl border border-border bg-surface p-8 shadow-lg shadow-black/20">
        <div className="mb-8">
          <p className="text-sm font-medium text-accent">Baseball Intel</p>
          <h1 className="mt-2 text-2xl font-semibold tracking-tight">
            {isRegister ? "Create your account" : "Sign in"}
          </h1>
          <p className="mt-2 text-sm text-muted">
            {isRegister
              ? "Register to view today's MLB schedule and game details."
              : "Sign in to access today's schedule from the API."}
          </p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <label className="block space-y-1.5">
            <span className="text-sm text-muted">Email</span>
            <input
              type="email"
              autoComplete="email"
              required
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none ring-accent/40 transition focus:border-accent focus:ring-2"
            />
          </label>

          <label className="block space-y-1.5">
            <span className="text-sm text-muted">Password</span>
            <input
              type="password"
              autoComplete={isRegister ? "new-password" : "current-password"}
              required
              minLength={isRegister ? 8 : undefined}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none ring-accent/40 transition focus:border-accent focus:ring-2"
            />
            {isRegister ? (
              <span className="text-xs text-muted">At least 8 characters</span>
            ) : null}
          </label>

          {error ? (
            <p className="rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-sm text-danger">
              {error}
            </p>
          ) : null}

          <button
            type="submit"
            disabled={submitting}
            className="w-full rounded-lg bg-accent px-4 py-2.5 text-sm font-medium text-accent-foreground transition hover:bg-accent/90 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {submitting
              ? "Please wait…"
              : isRegister
                ? "Create account"
                : "Sign in"}
          </button>
        </form>

        <p className="mt-6 text-center text-sm text-muted">
          {isRegister ? "Already have an account?" : "Need an account?"}{" "}
          <Link
            href={isRegister ? "/login" : "/register"}
            className="font-medium text-accent hover:underline"
          >
            {isRegister ? "Sign in" : "Register"}
          </Link>
        </p>
      </div>
    </div>
  );
}
