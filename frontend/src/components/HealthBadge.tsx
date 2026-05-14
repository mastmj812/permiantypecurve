import { useQuery } from "@tanstack/react-query";

interface HealthResponse {
  status: string;
  version: string;
}

async function fetchHealth(): Promise<HealthResponse> {
  const r = await fetch("/api/health");
  if (!r.ok) throw new Error(`Health check failed: ${r.status}`);
  return (await r.json()) as HealthResponse;
}

export function HealthBadge() {
  const { data, isError, isLoading } = useQuery({
    queryKey: ["health"],
    queryFn: fetchHealth,
    refetchInterval: 10_000,
  });

  if (isLoading) return <span className="health-badge warn">connecting…</span>;
  if (isError || !data) return <span className="health-badge error">backend down</span>;
  return <span className="health-badge ok">api ok · v{data.version}</span>;
}
