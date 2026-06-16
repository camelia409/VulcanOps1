import { BrowserRouter, Route, Routes } from "react-router-dom";
import PlatformPage from "./pages/PlatformPage";

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<PlatformPage />} />
      </Routes>
    </BrowserRouter>
  );
}
