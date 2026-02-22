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
  const [voiceProfiles, setVoiceProfiles] = useState<VoiceProfile[]>([]);
  const [currentProfile, setCurrentProfile] = useState<string>('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string>('');

  const requestVoiceProfiles = useCallback(async () => {
    if (!client) return;
    
    try {
      const response = await client.sendClientRequest('getVoiceProfiles');
      
      if (response.type === 'voiceProfiles' && response.status === 'success') {
        setVoiceProfiles(response.data);
        setLoading(false);
      } else if (response.status === 'error') {
        setError(response.message || 'Failed to load voice profiles');
        setLoading(false);
      }
    } catch (err) {
      console.error('Error requesting voice profiles:', err);
      setError('Failed to load voice profiles');
      setLoading(false);
    }
  }, [client]);

  const requestCurrentProfile = useCallback(async () => {
    if (!client) return;
    
    try {
      const response = await client.sendClientRequest('getCurrentVoiceProfile');
      
      if (response.type === 'currentVoiceProfile' && response.status === 'success') {
        setCurrentProfile(response.data?.name || '');
      }
    } catch (err) {
      console.error('Error requesting current voice profile:', err);
    }
  }, [client]);

  useEffect(() => {
    if (!client) return;

    const handleServerMessage = (message: any) => {
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
          } else if (message.status === 'error') {
            setError(message.message);
          }
          break;
          
        case 'error':
          setError(message.message);
          break;
      }
    };

    const handleTransportMessage = (event: any) => {
      try {
        const message = JSON.parse(event.data);
        
        // Check if this is an RTVI server response
        if (message.label === 'rtvi-ai' && message.type === 'server-response' && message.data) {
          handleServerMessage(message.data);
          return;
        }
        
        // Handle regular transport messages
        handleServerMessage(message);
      } catch {
        // Ignore parse errors for non-JSON messages
      }
    };

    const handleRtviMessage = (message: any) => {
      if (message.type === 'server-response' && message.data) {
        handleServerMessage(message.data);
      }
    };

    const handleBotReady = () => {
      requestVoiceProfiles();
      requestCurrentProfile();
    };

    const handleTransportStateChanged = (state: any) => {
      if (state === 'ready') {
        requestVoiceProfiles();
        requestCurrentProfile();
      }
    };

    // Register event listeners
    client.on('transportMessage', handleTransportMessage);
    client.on('rtviMessage', handleRtviMessage);
    client.on('botReady', handleBotReady);
    client.on('transportStateChanged', handleTransportStateChanged);

    // Request voice profiles if already connected
    if (client.connected) {
      requestVoiceProfiles();
      requestCurrentProfile();
    }

    return () => {
      client.off('transportMessage', handleTransportMessage);
      client.off('rtviMessage', handleRtviMessage);
      client.off('botReady', handleBotReady);
      client.off('transportStateChanged', handleTransportStateChanged);
    };
  }, [client, requestVoiceProfiles, requestCurrentProfile]);

  const handleProfileChange = async (profileName: string) => {
    if (!client || profileName === currentProfile) return;
    
    try {
      const response = await client.sendClientRequest('setVoiceProfile', {
        profileName
      });
      
      if (response.type === 'voiceProfileSet' && response.status === 'success') {
        setCurrentProfile(profileName);
      } else if (response.status === 'error') {
        setError(response.message || 'Failed to set voice profile');
      }
    } catch (err) {
      console.error('Error setting voice profile:', err);
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
