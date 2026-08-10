import { Route, Routes } from 'react-router';
import ErrorBoundary from './components/common/ErrorBoundary';
import AppShell from './components/layout/AppShell';
import Home from './pages/Home';
import Community from './pages/Community';
import Activity from './pages/Activity';
import Recommend from './pages/Recommend';
import Nearby from './pages/Nearby';
import History from './pages/History';
import Settings from './pages/Settings';
import About from './pages/About';
import Privacy from './pages/Privacy';
import Disclaimer from './pages/Disclaimer';
import NotFound from './pages/NotFound';
import Login from './pages/Login';
import AuthCallback from './pages/AuthCallback';
import { AuthProvider, ProtectedRoute } from './context/AuthContext';

export default function App() {
  return (
    <ErrorBoundary>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/auth/callback" element={<AuthCallback />} />
          <Route element={<AppShell />}>
            <Route index element={<Home />} />
            <Route path="community" element={<Community />} />
            <Route path="activities/:activityId" element={<Activity />} />
            <Route path="recommend" element={<Recommend />} />
            <Route path="nearby" element={<Nearby />} />
            <Route
              path="history"
              element={
                <ProtectedRoute>
                  <History />
                </ProtectedRoute>
              }
            />
            <Route
              path="settings"
              element={
                <ProtectedRoute>
                  <Settings />
                </ProtectedRoute>
              }
            />
            <Route path="about" element={<About />} />
            <Route path="privacy" element={<Privacy />} />
            <Route path="disclaimer" element={<Disclaimer />} />
            <Route path="*" element={<NotFound />} />
          </Route>
        </Routes>
      </AuthProvider>
    </ErrorBoundary>
  );
}
