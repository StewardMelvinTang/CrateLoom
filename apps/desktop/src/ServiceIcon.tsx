import {Link2} from 'lucide-react';
import spotify from './assets/services/spotify.svg';
import soundcloud from './assets/services/soundcloud.svg';
import youtube from './assets/services/youtube.svg';
import youtubeMusic from './assets/services/youtubemusic.svg';
import shazam from './assets/services/shazam.svg';
import musicbrainz from './assets/services/musicbrainz.svg';

const icons: Record<string, string> = {Spotify: spotify, SoundCloud: soundcloud, YouTube: youtube, 'YouTube Music': youtubeMusic, Shazam: shazam, MusicBrainz: musicbrainz};
export function ServiceIcon({name}: {name: string}) {
  return icons[name] ? <img className="service-icon" src={icons[name]} alt="" aria-hidden="true"/> : <Link2 size={14} aria-hidden="true"/>;
}
