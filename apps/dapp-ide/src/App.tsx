import { BrowserRouter, Routes, Route } from "react-router-dom";
import Home from "./pages/Home";
import IDE from "./pages/IDE";
import Deploy from "./pages/Deploy";
import Interact from "./pages/Interact";
import Settings from "./pages/Settings";

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/ide" element={<IDE />} />
        <Route path="/deploy" element={<Deploy />} />
        <Route path="/interact" element={<Interact />} />
        <Route path="/settings" element={<Settings />} />
      </Routes>
    </BrowserRouter>
  );
}

export default App;
