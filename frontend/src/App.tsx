import { Route, Routes } from "react-router-dom";
import MainLayout from "./layouts/MainLayout";
import RequireAuth from "./components/RequireAuth";
import Files from "./pages/Files";
import Home from "./pages/Home";
import Login from "./pages/Login";
import Register from "./pages/Register";

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/register" element={<Register />} />
      <Route element={<RequireAuth />}>
        <Route element={<MainLayout />}>
          <Route path="/" element={<Files />} />
          <Route path="/folders/:folderId" element={<Files />} />
          <Route path="/system" element={<Home />} />
        </Route>
      </Route>
    </Routes>
  );
}
