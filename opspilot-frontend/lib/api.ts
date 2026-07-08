const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export interface TraceStep {
  type: "tool_call" | "tool_result" | "message";
  tool?: string;
  arguments?: unknown;
  output?: unknown;
  text?: string;
}

export interface ChatResponse {
  reply: string;
  provider_used: string;
  trace: TraceStep[];
}

export interface Ec2Instance {
  instance_id: string;
  instance_type: string;
  state: string;
  availability_zone: string;
  public_ip: string | null;
  private_ip: string | null;
  launch_time: string | null;
  tags: Record<string, string>;
}

export interface MetricDatapoint {
  timestamp: string;
  average: number | null;
  maximum: number | null;
  unit: string;
}

export interface CpuUtilizationSummary {
  instance_id: string;
  lookback_hours: number;
  datapoints: MetricDatapoint[];
  average_cpu_percent: number | null;
  max_cpu_percent: number | null;
  breached_80_percent: boolean;
}

export interface Ec2ResourceCard {
  instance: Ec2Instance;
  cpu: CpuUtilizationSummary | null;
}

export interface ResourcesResponse {
  ec2: Ec2ResourceCard[];
}

export interface CloudTrailEvent {
  event_name: string;
  event_time: string;
  username: string | null;
}

export interface LambdaCard {
  functions: { name: string; runtime: string | null; last_modified: string | null }[];
  count: number;
}

export interface S3Card {
  buckets: { name: string; creation_date: string | null }[];
  count: number;
}

export interface DynamoCard {
  tables: { name: string; status: string; item_count: number | null }[];
  count: number;
}

export interface SnsCard {
  topics: { topic_arn: string; name: string }[];
  count: number;
}

export interface RdsCard {
  instances: { identifier: string; engine: string; instance_class: string; status: string }[];
  count: number;
}

export interface DashboardOverview {
  lambda_functions: LambdaCard;
  s3: S3Card;
  dynamodb: DynamoCard;
  sns: SnsCard;
  rds: RdsCard;
  cloudtrail: { events: CloudTrailEvent[] };
}

export interface Investigation {
  id: string;
  question: string;
  trace_summary: string;
  conclusion: string;
  created_at: string;
}

export interface InvestigationList {
  investigations: Investigation[];
}

export interface SimilarInvestigationResult extends Investigation {
  similarity: number;
}

export interface McpToolInfo {
  name: string;
  description: string | null;
}

export interface McpServerInfo {
  server_name: string;
  transport: string;
  tool_count: number;
  tools: McpToolInfo[];
}

class ApiError extends Error {
  constructor(message: string, public status?: number) {
    super(message);
    this.name = "ApiError";
  }
}

export async function sendChatMessage(message: string): Promise<ChatResponse> {
  const res = await fetch(`${API_BASE_URL}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
  });

  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new ApiError(body?.detail ?? `Request failed with status ${res.status}`, res.status);
  }

  return res.json();
}

export async function getEc2Resources(): Promise<ResourcesResponse> {
  const res = await fetch(`${API_BASE_URL}/resources/ec2`, { cache: "no-store" });

  if (!res.ok) {
    throw new ApiError(`Request failed with status ${res.status}`, res.status);
  }

  return res.json();
}

export async function getDashboardOverview(): Promise<DashboardOverview> {
  const res = await fetch(`${API_BASE_URL}/resources/overview`, { cache: "no-store" });

  if (!res.ok) {
    throw new ApiError(`Request failed with status ${res.status}`, res.status);
  }

  return res.json();
}

export async function getInvestigations(): Promise<InvestigationList> {
  const res = await fetch(`${API_BASE_URL}/investigations`, { cache: "no-store" });

  if (!res.ok) {
    throw new ApiError(`Request failed with status ${res.status}`, res.status);
  }

  return res.json();
}

export async function getMcpServerInfo(): Promise<McpServerInfo> {
  const res = await fetch(`${API_BASE_URL}/mcp/tools`, { cache: "no-store" });

  if (!res.ok) {
    throw new ApiError(`Request failed with status ${res.status}`, res.status);
  }

  return res.json();
}
