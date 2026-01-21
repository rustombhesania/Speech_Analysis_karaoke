"""
Karaoke Backend Server with Demucs (Modern Alternative)
Install: pip install flask flask-cors yt-dlp demucs syncedlyrics torch
"""

from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import yt_dlp
import os
import json
from syncedlyrics import search
import tempfile
import shutil
import subprocess
import glob

app = Flask(__name__)
CORS(app)

DOWNLOAD_FOLDER = 'downloads'
OUTPUT_FOLDER = 'output'
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)


@app.route('/api/process', methods=['POST'])
def process_song():
    """Process YouTube URL: download, separate vocals, get lyrics"""
    data = request.json
    youtube_url = data.get('url')
    
    if not youtube_url:
        return jsonify({'error': 'No URL provided'}), 400
    
    try:
        # Step 1: Download audio from YouTube
        print("Downloading audio...")
        audio_path, video_info = download_audio(youtube_url)
        
        # Step 2: Separate vocals and instrumental using Demucs
        print("Separating vocals with Demucs...")
        vocal_path, instrumental_path = separate_audio_demucs(audio_path)
        
        # Step 3: Get synced lyrics
        print("Fetching lyrics...")
        lyrics = get_lyrics(video_info['title'], video_info.get('artist', ''))
        
        return jsonify({
            'success': True,
            'title': video_info['title'],
            'artist': video_info.get('artist', 'Unknown'),
            'duration': video_info.get('duration', 0),
            'vocal_url': f'/audio/vocals/{os.path.basename(vocal_path)}',
            'instrumental_url': f'/audio/instrumental/{os.path.basename(instrumental_path)}',
            'lyrics': lyrics
        })
        
    except Exception as e:
        print(f"Error: {str(e)}")
        return jsonify({'error': str(e)}), 500


def download_audio(url):
    """Download audio from YouTube"""
    temp_dir = tempfile.mkdtemp(dir=DOWNLOAD_FOLDER)
    
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': os.path.join(temp_dir, '%(title)s.%(ext)s'),
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
        }],
        'quiet': False,
    }
    
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        # Find the downloaded file
        audio_file = None
        for ext in ['.mp3', '.m4a', '.webm']:
            potential_file = ydl.prepare_filename(info).replace('.webm', ext).replace('.m4a', ext)
            if os.path.exists(potential_file):
                audio_file = potential_file
                break
        
        if not audio_file:
            # Search for any audio file in the directory
            files = glob.glob(os.path.join(temp_dir, '*'))
            if files:
                audio_file = files[0]
        
        return audio_file, {
            'title': info.get('title', 'Unknown'),
            'artist': info.get('artist', info.get('uploader', 'Unknown')),
            'duration': info.get('duration', 0)
        }


def separate_audio_demucs(audio_path):
    """Separate vocals and instrumental using Demucs CLI"""
    output_dir = tempfile.mkdtemp(dir=OUTPUT_FOLDER)
    
    try:
        # Run Demucs separation (using htdemucs model - high quality)
        cmd = [
            'demucs',
            '--two-stems', 'vocals',  # Only separate vocals from the rest
            '--out', output_dir,
            '--mp3',  # Output as MP3 for smaller file size
            '--mp3-bitrate', '320',  # High quality
            audio_path
        ]
        
        print(f"Running: {' '.join(cmd)}")
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        print("Demucs completed successfully")
        
        # Find the output files
        # Demucs creates: output_dir/htdemucs/songname/vocals.mp3 and no_vocals.mp3
        base_name = os.path.splitext(os.path.basename(audio_path))[0]
        separated_dir = os.path.join(output_dir, 'htdemucs', base_name)
        
        vocal_path = os.path.join(separated_dir, 'vocals.mp3')
        instrumental_path = os.path.join(separated_dir, 'no_vocals.mp3')
        
        # Verify files exist
        if not os.path.exists(vocal_path):
            raise FileNotFoundError(f"Vocals file not found at {vocal_path}")
        if not os.path.exists(instrumental_path):
            raise FileNotFoundError(f"Instrumental file not found at {instrumental_path}")
        
        return vocal_path, instrumental_path
        
    except subprocess.CalledProcessError as e:
        print(f"Demucs error: {e.stderr}")
        raise Exception(f"Demucs separation failed: {e.stderr}")
    except Exception as e:
        print(f"Separation error: {str(e)}")
        raise


def get_lyrics(title, artist):
    """Fetch synced lyrics"""
    try:
        # Try to get synced lyrics
        lrc = search(f"{artist} {title}")
        
        if lrc:
            # Parse LRC format
            lyrics = parse_lrc(lrc)
            return lyrics
        else:
            print("No synced lyrics found")
            return []
            
    except Exception as e:
        print(f"Lyrics error: {e}")
        return []


def parse_lrc(lrc_content):
    """Parse LRC lyrics format"""
    lyrics = []
    lines = lrc_content.strip().split('\n')
    
    for line in lines:
        if '[' in line and ']' in line:
            try:
                # Extract timestamp [mm:ss.xx]
                time_str = line[line.index('[')+1:line.index(']')]
                text = line[line.index(']')+1:].strip()
                
                if text and ':' in time_str:
                    # Convert to seconds
                    parts = time_str.split(':')
                    minutes = int(parts[0])
                    seconds = float(parts[1])
                    total_seconds = minutes * 60 + seconds
                    
                    lyrics.append({
                        'time': total_seconds,
                        'text': text
                    })
            except:
                continue
    
    return lyrics


@app.route('/api/audio/<track_type>/<path:filename>')
def serve_audio(track_type, filename):
    """Serve audio files"""
    # Search for the file in output folder
    for root, dirs, files in os.walk(OUTPUT_FOLDER):
        if filename in files:
            file_path = os.path.join(root, filename)
            return send_file(file_path, mimetype='audio/mpeg')
    
    return jsonify({'error': 'File not found'}), 404


@app.route('/api/cleanup', methods=['POST'])
def cleanup():
    """Clean up old files"""
    try:
        shutil.rmtree(DOWNLOAD_FOLDER)
        shutil.rmtree(OUTPUT_FOLDER)
        os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)
        os.makedirs(OUTPUT_FOLDER, exist_ok=True)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/health')
def health():
    """Health check endpoint"""
    return jsonify({'status': 'ok', 'message': 'Karaoke server running with Demucs'})


if __name__ == '__main__':
    print("=" * 60)
    print("🎤 Karaoke Server Starting...")
    print("=" * 60)
    print("Using Demucs for vocal separation")
    print("Make sure you have:")
    print("  ✓ FFmpeg installed")
    print("  ✓ torch and demucs installed")
    print("=" * 60)
    app.run(debug=True, port=5000, threaded=True)