import { Navigate, Outlet } from "react-router-dom";
import { useAuthStore } from "../stores/auth";

/** 路由守卫：未登录访问受保护页面 → 跳转登录页。 */
export default function RequireAuth() {
  const accessToken = useAuthStore((s) => s.accessToken);
  const user = useAuthStore((s) => s.user);
  if (!accessToken || !user) {
    return <Navigate to="/login" replace />;
  }
  return <Outlet />;
}
