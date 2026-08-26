import React, { useEffect, useState } from 'react';

export default function SplashScreen({ onReady }: { onReady: () => void }) {
  const [status, setStatus] = useState<string>('Initializing...');

  useEffect(() => {
    let isMounted = true;
    let attempt = 0;

    const checkHealth = async () => {
      try {
        attempt++;
        if (attempt > 3) setStatus('Preloading AI models into memory...');
        if (attempt > 10) setStatus('Starting local databases...');

        const res = await fetch('http://127.0.0.1:8000/api/health', {
          method: 'GET',
          headers: { 'Cache-Control': 'no-cache' }
        });
        
        if (res.ok) {
          if (isMounted) onReady();
        } else {
          if (isMounted) setTimeout(checkHealth, 1000);
        }
      } catch (e) {
        if (isMounted) setTimeout(checkHealth, 1000);
      }
    };

    checkHealth();

    return () => {
      isMounted = false;
    };
  }, [onReady]);

  return (
    <div className="flex h-screen w-screen flex-col items-center justify-center bg-[#080B14] text-white">
      <div className="flex flex-col items-center space-y-6">
        {/* Pulsing Logo or Spinner */}
        <div className="relative flex items-center justify-center h-24 w-24">
          <div className="absolute inset-0 rounded-full border-t-4 border-b-4 border-blue-500 animate-spin"></div>
          <div className="absolute inset-2 rounded-full border-r-4 border-l-4 border-purple-500 animate-spin" style={{ animationDelay: '0.2s', animationDirection: 'reverse' }}></div>
          {/* Hexagon icon or Aegis logo */}
          <svg className="w-10 h-10 text-white z-10" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 11c0 3.517-1.009 6.799-2.753 9.571m-3.44-2.04l.054-.09A13.916 13.916 0 008 11a4 4 0 118 0c0 1.017-.07 2.019-.203 3m-2.118 6.844A21.88 21.88 0 0015.171 17m3.839 1.132c.645-2.266.99-4.659.99-7.132A8 8 0 008 4.07M3 15.364c.64-1.319 1-2.8 1-4.364 0-1.457.39-2.823 1.07-4" />
          </svg>
        </div>
        
        <h1 className="text-2xl font-bold tracking-widest text-transparent bg-clip-text bg-gradient-to-r from-blue-400 to-purple-500">
          AEGIS
        </h1>
        <p className="text-gray-400 text-sm animate-pulse">{status}</p>
      </div>
    </div>
  );
}
