import { useEffect, useState, useCallback } from 'react';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@pipecat-ai/voice-ui-kit';

interface VoiceProfile {
  name: string;
  description: string;
}

interface VoiceProfileSelectProps {
  client?: any;
  disabled?: boolean;
}

export const VoiceProfileSelect = ({ client, disabled = false }: VoiceProfileSelectProps) => {
  console.log('VoiceProfileSelect rendering, client:', !!client);
  
  const [voiceProfiles, setVoiceProfiles] = useState<VoiceProfile[]>([]);
  const [currentProfile, setCurrentProfile] = useState<string>('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string>('');

  const requestVoiceProfiles = useCallback(async () => {
    if (!client) return;
    
    try {
      console.log('Requesting voice profiles using sendClientRequest');
      const response = await client.sendClientRequest('getVoiceProfiles');
      console.log('Voice profiles response:', response);
      
      // Process the response
      if (response.type === 'voiceProfiles' && response.status === 'success') {
        setVoiceProfiles(response.data);
        setLoading(false);
        console.log('✅ Voice profiles loaded:', response.data);
      } else if (response.status === 'error') {
        setError(response.message || 'Failed to load voice profiles');
        setLoading(false);
      }
    } catch (error) {
      console.error('Error requesting voice profiles:', error);
      setError('Failed to load voice profiles');
      setLoading(false);
    }
  }, [client]);

  const requestCurrentProfile = useCallback(async () => {
    if (!client) return;
    
    try {
      console.log('Requesting current voice profile using sendClientRequest');
      const response = await client.sendClientRequest('getCurrentVoiceProfile');
      console.log('Current profile response:', response);
      
      // Process the response
      if (response.type === 'currentVoiceProfile' && response.status === 'success') {
        setCurrentProfile(response.data?.name || '');
        console.log('✅ Current profile loaded:', response.data?.name);
      } else if (response.status === 'error') {
        console.error('Failed to get current profile:', response.message);
      }
    } catch (error) {
      console.error('Error requesting current voice profile:', error);
    }
  }, [client]);

  useEffect(() => {
    if (!client) return;

    console.log('Setting up VoiceProfileSelect event listeners');

    const handleTransportMessage = (event: any) => {
      console.log('Received transport message:', event.data);
      try {
        const message = JSON.parse(event.data);
        
        // Check if this is an RTVI server response
        if (message.label === 'rtvi-ai' && message.type === 'server-response' && message.data) {
          console.log('🔥 FOUND RTVI SERVER RESPONSE IN TRANSPORT:', message.data);
          handleServerMessage(message.data);
          return;
        }
        
        // Handle regular transport messages
        switch (message.type) {
          case 'voiceProfiles':
            if (message.status === 'success') {
              setVoiceProfiles(message.data);
              setLoading(false);
            } else if (message.status === 'error') {
              setError(message.message);
              setLoading(false);
            }
            break;
            
          case 'currentVoiceProfile':
            if (message.status === 'success') {
              setCurrentProfile(message.data?.name || '');
            }
            break;
            
          case 'voiceProfileSet':
            if (message.status === 'success') {
              setCurrentProfile(message.data.name);
              console.log('Voice profile set successfully:', message.data.name);
            } else if (message.status === 'error') {
              setError(message.message);
            }
            break;
            
          case 'error':
            setError(message.message);
            break;
        }
      } catch (err) {
        console.error('Failed to parse transport message:', err);
      }
    };

    const handleServerMessage = (message: any) => {
      console.log('Received server message:', message);
      try {
        switch (message.type) {
          case 'voiceProfiles':
            if (message.status === 'success') {
              setVoiceProfiles(message.data);
              setLoading(false);
            } else if (message.status === 'error') {
              setError(message.message);
              setLoading(false);
            }
            break;
            
          case 'currentVoiceProfile':
            if (message.status === 'success') {
              setCurrentProfile(message.data?.name || '');
            }
            break;
            
          case 'voiceProfileSet':
            if (message.status === 'success') {
              setCurrentProfile(message.data.name);
              console.log('Voice profile set successfully:', message.data.name);
            } else if (message.status === 'error') {
              setError(message.message);
            }
            break;
            
          case 'error':
            setError(message.message);
            break;
        }
      } catch (err) {
        console.error('Failed to parse server message:', err);
      }
    };

    const handleBotReady = () => {
      console.log('Bot ready, requesting voice profiles');
      requestVoiceProfiles();
      requestCurrentProfile();
    };

    const handleConnected = () => {
      console.log('Client connected');
    };

    const handleTransportStateChanged = (state: any) => {
      console.log('Transport state changed:', state);
      // When transport becomes ready, request voice profiles
      if (state === 'ready') {
        console.log('Transport ready, requesting voice profiles');
        requestVoiceProfiles();
        requestCurrentProfile();
      }
    };

    // Debug: Listen to ALL possible events
    const debugEventHandler = (eventName: string) => (data: any) => {
      console.log(`🔥 EVENT FIRED: ${eventName}`, data);
      if (eventName.includes('message') || eventName.includes('response')) {
        console.log(`🔥 MESSAGE DATA:`, JSON.stringify(data, null, 2));
      }
    };

    // Listen for transport messages
    client.on('transportMessage', handleTransportMessage);
    
    // Try to listen to raw RTVI messages through different events
    client.on('rtviMessage', (message) => {
      console.log('🔥 RTVI Message received:', message);
      if (message.type === 'server-response' && message.data) {
        console.log('🔥 Processing RTVI server response:', message.data);
        handleServerMessage(message.data);
      }
    });
    
    // Debug: Listen for any other message-related events
    ['message', 'response', 'data', 'rtvi-response', 'server-response'].forEach(eventName => {
      client.on(eventName, debugEventHandler(eventName));
    });
    
    // Listen for bot ready event
    client.on('botReady', handleBotReady);
    
    // Listen for connection events
    client.on('connected', handleConnected);
    client.on('transportStateChanged', handleTransportStateChanged);

    // Request voice profiles if bot is already ready
    if (client.connected) {
      console.log('Client already connected, requesting profiles');
      requestVoiceProfiles();
      requestCurrentProfile();
    }

    return () => {
      client.off('transportMessage', handleTransportMessage);
      // Note: onServerMessage doesn't have a corresponding off method in some versions
      
      // Remove debug event listeners
      ['message', 'response', 'data', 'rtvi-response', 'server-response'].forEach(eventName => {
        client.off(eventName, debugEventHandler(eventName));
      });
      
      client.off('botReady', handleBotReady);
      client.off('connected', handleConnected);
      client.off('transportStateChanged', handleTransportStateChanged);
    };
  }, [client, requestVoiceProfiles, requestCurrentProfile]);

  const handleProfileChange = async (profileName: string) => {
    if (!client || profileName === currentProfile) return;
    
    try {
      console.log('Setting voice profile using sendClientRequest:', profileName);
      const response = await client.sendClientRequest('setVoiceProfile', {
        profileName
      });
      console.log('Set profile response:', response);
      
      // Update UI when server confirms the change
      if (response.type === 'voiceProfileSet' && response.status === 'success') {
        setCurrentProfile(profileName);
        console.log('✅ Voice profile changed to:', profileName);
        
        // Reconnect to apply the new voice profile
        console.log('🔄 Reconnecting to apply new voice profile...');
        if (client.disconnect) {
          await client.disconnect();
          // Wait a moment then reconnect
          setTimeout(() => {
            if (client.connect) {
              client.connect();
            }
          }, 1000);
        }
      } else if (response.status === 'error') {
        setError(response.message || 'Failed to set voice profile');
      }
    } catch (error) {
      console.error('Error setting voice profile:', error);
      setError('Failed to set voice profile');
    }
  };

  if (loading) {
    return (
      <div className="flex items-center gap-2" data-testid="voice-profile-loading">
        <span className="text-sm text-gray-500">Loading voice profiles...</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center gap-2 text-red-500 text-sm">
        <span>Voice profiles unavailable</span>
      </div>
    );
  }

  return (
    <div className="flex items-center gap-2">
      <label htmlFor="voice-profile-select" className="text-sm font-medium text-gray-700">
        Voice:
      </label>
      <Select
        value={currentProfile}
        onValueChange={handleProfileChange}
        disabled={disabled || voiceProfiles.length === 0}
      >
        <SelectTrigger className="w-48" id="voice-profile-select">
          <SelectValue placeholder="Select voice profile" />
        </SelectTrigger>
        <SelectContent>
          {voiceProfiles.map((profile) => (
            <SelectItem key={profile.name} value={profile.name}>
              <div className="flex flex-col">
                <span className="font-medium">{profile.name}</span>
                <span className="text-xs text-gray-500">{profile.description}</span>
              </div>
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
};
