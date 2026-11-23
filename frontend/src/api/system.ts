/** 系统接口：/ping 与 /health（503 时也要拿到组件明细，故放开 validateStatus）。 */
import { request } from "./request";

export interface HealthData {
  status: "ok" | "degraded";
  components: Record<string, string>;
  app: string;
  version: string;
  env: string;
}

export async function getHealth(): Promise<HealthData> {
  const body = await request.get("/health", { validateStatus: () => true });
  // 拦截器对 code=0 的 503 响应同样返回 data
  return body as unknown as HealthData;
}
