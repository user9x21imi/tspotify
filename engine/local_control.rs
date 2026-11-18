    /// tspotify's synchronous local IPC: controls librespot, never Spotify Web API.
    pub fn tspotify_local_control(&self, command: &str, value: i64) -> Result<Vec<u8>> {
        if command == "capabilities" {
            return Ok(serde_json::to_vec(&serde_json::json!({
                "protocol": 1, "local_volume": true, "local_seek": true,
                "local_transport": true, "local_load": true, "engine": "tspotify-librespot"
            }))?);
        }
        #[cfg(feature = "streaming")]
        {
            let connection = self.stream_conn.lock();
            let spirc = connection.as_ref().context("Integrated playback is still connecting")?;
            if let Some(json) = command.strip_prefix("load:") {
                use librespot_connect::{LoadRequest, LoadRequestOptions, PlayingTrack};
                let payload: serde_json::Value = serde_json::from_str(json)?;
                let uri = payload["uri"].as_str().context("Missing track URI")?;
                anyhow::ensure!(uri.starts_with("spotify:track:") && uri.len() == 36, "Invalid track URI");
                let options = LoadRequestOptions {
                    start_playing: true,
                    seek_to: value.clamp(0, u32::MAX as i64) as u32,
                    playing_track: Some(PlayingTrack::Uri(uri.to_owned())),
                    ..Default::default()
                };
                let request = match payload["context_uri"].as_str() {
                    Some(context) if context.starts_with("spotify:playlist:") || context.starts_with("spotify:album:") =>
                        LoadRequest::from_context_uri(context.to_owned(), options),
                    _ => LoadRequest::from_tracks(vec![uri.to_owned()], options),
                };
                spirc.activate()?;
                spirc.load(request)?;
                return Ok(serde_json::to_vec(&serde_json::json!({"accepted": true, "local": true}))?);
            }
            match command {
                "volume" => spirc.set_volume((value.clamp(0, 100) as u32 * 65535 / 100) as u16)?,
                "seek" => spirc.set_position_ms(value.clamp(0, u32::MAX as i64) as u32)?,
                "play" => spirc.play()?,
                "pause" => spirc.pause()?,
                "play-pause" => spirc.play_pause()?,
                "next" => spirc.next()?,
                "previous" => spirc.prev()?,
                _ => anyhow::bail!("Unsupported local control: {command}"),
            }
            return Ok(serde_json::to_vec(&serde_json::json!({"accepted": true, "local": true}))?);
        }
        #[cfg(not(feature = "streaming"))]
        anyhow::bail!("This engine was built without streaming")
    }
