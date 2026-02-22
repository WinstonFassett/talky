import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';

// Simple test to see if React works at all
const TestApp = () => {
  return (
    <div>
      <h1>Test App Working</h1>
      <p>If you see this, React is working</p>
    </div>
  );
};

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <TestApp />
  </StrictMode>
);
