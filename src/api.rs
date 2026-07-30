use std::{pin::Pin, sync::Arc, time::Duration};

use futures_core::Stream;
use tokio::time::MissedTickBehavior;
use tonic::{Request, Response, Status};

use crate::{
    app::{App, AppError},
    proto::v1::{
        AudioAnalysis, ClearRecordingRequest, ClearRecordingResponse,
        ConnectBluetoothReceiverDeviceRequest, ConnectBluetoothReceiverDeviceResponse,
        ControlShowRequest, ControlShowResponse, DisconnectBluetoothReceiverDeviceRequest,
        DisconnectBluetoothReceiverDeviceResponse, ExportConfigRequest, ExportConfigResponse,
        ForgetBluetoothReceiverDeviceRequest, ForgetBluetoothReceiverDeviceResponse,
        GetBluetoothReceiverStatusRequest, GetBluetoothReceiverStatusResponse, GetConfigRequest,
        GetConfigResponse, GetSnapshotRequest, GetSnapshotResponse, ImportConfigRequest,
        ImportConfigResponse, ImportGrandMa2FixtureRequest, ImportGrandMa2FixtureResponse,
        ListAudioDevicesRequest, ListAudioDevicesResponse, ListGrandMa2FixtureTypesRequest,
        ListGrandMa2FixtureTypesResponse, LiveAudioFrame, LiveFrame, ResetConfigRequest,
        ResetConfigResponse, SetBlackoutRequest, SetBlackoutResponse,
        SetBluetoothReceiverPairingRequest, SetBluetoothReceiverPairingResponse, ShowSnapshot,
        StartRecordingRequest, StartRecordingResponse, StopRecordingRequest, StopRecordingResponse,
        UpdateConfigRequest, UpdateConfigResponse, WatchLiveFramesRequest, WatchLiveFramesResponse,
        WatchSnapshotsRequest, WatchSnapshotsResponse,
        music_auto_show_service_server::MusicAutoShowService,
    },
};

pub struct GrpcApi {
    app: Arc<App>,
}

impl GrpcApi {
    pub fn new(app: Arc<App>) -> Self {
        Self { app }
    }
}

type SnapshotStream = Pin<Box<dyn Stream<Item = Result<WatchSnapshotsResponse, Status>> + Send>>;
type LiveFrameStream = Pin<Box<dyn Stream<Item = Result<WatchLiveFramesResponse, Status>> + Send>>;

fn live_frame(snapshot: &ShowSnapshot) -> LiveFrame {
    LiveFrame {
        sequence: snapshot.sequence,
        captured_at_unix_ms: snapshot.captured_at_unix_ms,
        audio: snapshot.audio.as_ref().map(live_audio_frame),
        fixture_states: snapshot.fixture_states.clone(),
        effect_runtime: snapshot.effect_runtime.clone(),
    }
}

fn live_audio_frame(audio: &AudioAnalysis) -> LiveAudioFrame {
    LiveAudioFrame {
        energy: audio.energy,
        rms: audio.rms,
        bass: audio.bass,
        mid: audio.mid,
        high: audio.high,
        tempo: audio.tempo,
        beat_detected: audio.beat_detected,
        downbeat_detected: audio.downbeat_detected,
        beat_confidence: audio.beat_confidence,
        beat_position: audio.beat_position,
        bar_position: audio.bar_position,
        estimated_beat: audio.estimated_beat,
        estimated_bar: audio.estimated_bar,
        waveform: audio.waveform.clone(),
        spectrum: audio.spectrum.clone(),
        meter: audio.meter,
        beat_index: audio.beat_index,
        beat_activation: audio.beat_activation,
        downbeat_activation: audio.downbeat_activation,
        tracking_confidence: audio.tracking_confidence,
        rms_dbfs: audio.rms_dbfs,
        peak_dbfs: audio.peak_dbfs,
        clipping: audio.clipping,
        spectrum_min_hz: audio.spectrum_min_hz,
        spectrum_max_hz: audio.spectrum_max_hz,
    }
}

#[tonic::async_trait]
impl MusicAutoShowService for GrpcApi {
    type WatchSnapshotsStream = SnapshotStream;
    type WatchLiveFramesStream = LiveFrameStream;

    async fn get_snapshot(
        &self,
        _request: Request<GetSnapshotRequest>,
    ) -> Result<Response<GetSnapshotResponse>, Status> {
        Ok(Response::new(GetSnapshotResponse {
            snapshot: Some(self.app.snapshot().await),
        }))
    }

    async fn watch_snapshots(
        &self,
        request: Request<WatchSnapshotsRequest>,
    ) -> Result<Response<Self::WatchSnapshotsStream>, Status> {
        let interval =
            Duration::from_millis(request.into_inner().interval_ms.clamp(25, 5_000) as u64);
        let mut receiver = self.app.subscribe();
        let app = Arc::clone(&self.app);
        let stream = async_stream::stream! {
            let initial = Arc::clone(&receiver.borrow_and_update().snapshot);
            yield Ok(WatchSnapshotsResponse { snapshot: Some(initial.as_ref().clone()) });
            let mut ticker = tokio::time::interval_at(tokio::time::Instant::now() + interval, interval);
            ticker.set_missed_tick_behavior(MissedTickBehavior::Skip);
            let mut pending = None;
            loop {
                tokio::select! {
                    () = app.wait_for_shutdown() => break,
                    changed = receiver.changed() => {
                        if changed.is_err() {
                            yield Err(Status::unavailable("show state stream closed"));
                            break;
                        }
                        pending = Some(Arc::clone(&receiver.borrow_and_update().snapshot));
                    }
                    _ = ticker.tick(), if pending.is_some() => {
                        if let Some(snapshot) = pending.take() {
                            yield Ok(WatchSnapshotsResponse {
                                snapshot: Some(snapshot.as_ref().clone()),
                            });
                        }
                    }
                }
            }
        };
        Ok(Response::new(Box::pin(stream)))
    }

    async fn watch_live_frames(
        &self,
        request: Request<WatchLiveFramesRequest>,
    ) -> Result<Response<Self::WatchLiveFramesStream>, Status> {
        let interval =
            Duration::from_millis(request.into_inner().interval_ms.clamp(16, 1_000) as u64);
        let mut receiver = self.app.subscribe();
        let app = Arc::clone(&self.app);
        let stream = async_stream::stream! {
            let initial = Arc::clone(&receiver.borrow_and_update().snapshot);
            yield Ok(WatchLiveFramesResponse { frame: Some(live_frame(&initial)) });
            let mut ticker = tokio::time::interval_at(tokio::time::Instant::now() + interval, interval);
            ticker.set_missed_tick_behavior(MissedTickBehavior::Skip);
            let mut pending = None;
            loop {
                tokio::select! {
                    () = app.wait_for_shutdown() => break,
                    changed = receiver.changed() => {
                        if changed.is_err() {
                            yield Err(Status::unavailable("show state stream closed"));
                            break;
                        }
                        pending = Some(Arc::clone(&receiver.borrow_and_update().snapshot));
                    }
                    _ = ticker.tick(), if pending.is_some() => {
                        if let Some(snapshot) = pending.take() {
                            yield Ok(WatchLiveFramesResponse {
                                frame: Some(live_frame(&snapshot)),
                            });
                        }
                    }
                }
            }
        };
        Ok(Response::new(Box::pin(stream)))
    }

    async fn get_config(
        &self,
        _request: Request<GetConfigRequest>,
    ) -> Result<Response<GetConfigResponse>, Status> {
        Ok(Response::new(GetConfigResponse {
            config: Some(self.app.config().await),
        }))
    }

    async fn update_config(
        &self,
        request: Request<UpdateConfigRequest>,
    ) -> Result<Response<UpdateConfigResponse>, Status> {
        let config = request
            .into_inner()
            .config
            .ok_or_else(|| Status::invalid_argument("config is required"))?;
        let config = self.app.update_config(config).await.map_err(app_status)?;
        Ok(Response::new(UpdateConfigResponse {
            config: Some(config),
        }))
    }

    async fn export_config(
        &self,
        _request: Request<ExportConfigRequest>,
    ) -> Result<Response<ExportConfigResponse>, Status> {
        let (json, filename) = self.app.export_config().await.map_err(app_status)?;
        Ok(Response::new(ExportConfigResponse { json, filename }))
    }

    async fn import_config(
        &self,
        request: Request<ImportConfigRequest>,
    ) -> Result<Response<ImportConfigResponse>, Status> {
        let json = request.into_inner().json;
        if json.trim().is_empty() {
            return Err(Status::invalid_argument("configuration JSON is required"));
        }
        let config = self.app.import_config(&json).await.map_err(app_status)?;
        Ok(Response::new(ImportConfigResponse {
            config: Some(config),
        }))
    }

    async fn reset_config(
        &self,
        _request: Request<ResetConfigRequest>,
    ) -> Result<Response<ResetConfigResponse>, Status> {
        let config = self.app.reset_config().await.map_err(app_status)?;
        Ok(Response::new(ResetConfigResponse {
            config: Some(config),
        }))
    }

    async fn list_audio_devices(
        &self,
        _request: Request<ListAudioDevicesRequest>,
    ) -> Result<Response<ListAudioDevicesResponse>, Status> {
        Ok(Response::new(ListAudioDevicesResponse {
            devices: self.app.audio_devices().await.map_err(app_status)?,
        }))
    }

    async fn get_bluetooth_receiver_status(
        &self,
        _request: Request<GetBluetoothReceiverStatusRequest>,
    ) -> Result<Response<GetBluetoothReceiverStatusResponse>, Status> {
        Ok(Response::new(GetBluetoothReceiverStatusResponse {
            status: Some(self.app.bluetooth_receiver_status().await),
        }))
    }

    async fn set_bluetooth_receiver_pairing(
        &self,
        request: Request<SetBluetoothReceiverPairingRequest>,
    ) -> Result<Response<SetBluetoothReceiverPairingResponse>, Status> {
        let request = request.into_inner();
        let status = self
            .app
            .set_bluetooth_receiver_pairing(request.enabled, request.timeout_seconds)
            .await
            .map_err(app_status)?;
        Ok(Response::new(SetBluetoothReceiverPairingResponse {
            status: Some(status),
        }))
    }

    async fn connect_bluetooth_receiver_device(
        &self,
        request: Request<ConnectBluetoothReceiverDeviceRequest>,
    ) -> Result<Response<ConnectBluetoothReceiverDeviceResponse>, Status> {
        let device_id = required_device_id(request.into_inner().device_id)?;
        let status = self
            .app
            .connect_bluetooth_receiver_device(&device_id)
            .await
            .map_err(app_status)?;
        Ok(Response::new(ConnectBluetoothReceiverDeviceResponse {
            status: Some(status),
        }))
    }

    async fn disconnect_bluetooth_receiver_device(
        &self,
        request: Request<DisconnectBluetoothReceiverDeviceRequest>,
    ) -> Result<Response<DisconnectBluetoothReceiverDeviceResponse>, Status> {
        let device_id = required_device_id(request.into_inner().device_id)?;
        let status = self
            .app
            .disconnect_bluetooth_receiver_device(&device_id)
            .await
            .map_err(app_status)?;
        Ok(Response::new(DisconnectBluetoothReceiverDeviceResponse {
            status: Some(status),
        }))
    }

    async fn forget_bluetooth_receiver_device(
        &self,
        request: Request<ForgetBluetoothReceiverDeviceRequest>,
    ) -> Result<Response<ForgetBluetoothReceiverDeviceResponse>, Status> {
        let device_id = required_device_id(request.into_inner().device_id)?;
        let status = self
            .app
            .forget_bluetooth_receiver_device(&device_id)
            .await
            .map_err(app_status)?;
        Ok(Response::new(ForgetBluetoothReceiverDeviceResponse {
            status: Some(status),
        }))
    }

    async fn list_grand_ma2_fixture_types(
        &self,
        _request: Request<ListGrandMa2FixtureTypesRequest>,
    ) -> Result<Response<ListGrandMa2FixtureTypesResponse>, Status> {
        Ok(Response::new(ListGrandMa2FixtureTypesResponse {
            fixture_types: self.app.grandma2_fixture_types().await,
        }))
    }

    async fn import_grand_ma2_fixture(
        &self,
        request: Request<ImportGrandMa2FixtureRequest>,
    ) -> Result<Response<ImportGrandMa2FixtureResponse>, Status> {
        let request = request.into_inner();
        let (config, fixture_types) = self
            .app
            .import_grandma2_fixture(&request.filename, &request.xml)
            .await
            .map_err(app_status)?;
        Ok(Response::new(ImportGrandMa2FixtureResponse {
            config: Some(config),
            fixture_types,
        }))
    }

    async fn control_show(
        &self,
        request: Request<ControlShowRequest>,
    ) -> Result<Response<ControlShowResponse>, Status> {
        let command = request.into_inner().command();
        let result = self.app.control(command).await.map_err(app_status)?;
        Ok(Response::new(ControlShowResponse {
            result: Some(result),
        }))
    }

    async fn set_blackout(
        &self,
        request: Request<SetBlackoutRequest>,
    ) -> Result<Response<SetBlackoutResponse>, Status> {
        let result = self
            .app
            .set_blackout(request.into_inner().enabled)
            .await
            .map_err(app_status)?;
        Ok(Response::new(SetBlackoutResponse {
            result: Some(result),
        }))
    }

    async fn start_recording(
        &self,
        _request: Request<StartRecordingRequest>,
    ) -> Result<Response<StartRecordingResponse>, Status> {
        let status = self.app.start_recording().await.map_err(app_status)?;
        Ok(Response::new(StartRecordingResponse {
            status: Some(status),
        }))
    }

    async fn stop_recording(
        &self,
        _request: Request<StopRecordingRequest>,
    ) -> Result<Response<StopRecordingResponse>, Status> {
        let recording = self.app.stop_recording().await.map_err(app_status)?;
        Ok(Response::new(StopRecordingResponse {
            recording: Some(recording),
        }))
    }

    async fn clear_recording(
        &self,
        _request: Request<ClearRecordingRequest>,
    ) -> Result<Response<ClearRecordingResponse>, Status> {
        let status = self.app.clear_recording().await.map_err(app_status)?;
        Ok(Response::new(ClearRecordingResponse {
            status: Some(status),
        }))
    }
}

fn required_device_id(device_id: String) -> Result<String, Status> {
    let device_id = device_id.trim();
    if device_id.is_empty() {
        return Err(Status::invalid_argument("Bluetooth device ID is required"));
    }
    Ok(device_id.into())
}

fn app_status(error: AppError) -> Status {
    let message = error.to_string();
    match error {
        AppError::Config(error) if error.is_invalid_input() => Status::invalid_argument(message),
        AppError::FailedPrecondition(_) => Status::failed_precondition(message),
        AppError::ResourceExhausted => Status::resource_exhausted(message),
        AppError::Unavailable | AppError::Runtime(_) => Status::unavailable(message),
        AppError::Config(_) => Status::internal(message),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::proto::v1::{AnalysisHistoryFrame, SpectrogramFrame};
    use prost::Message;
    use tokio_stream::StreamExt;

    const IMPORTED_FIXTURE: &[u8] = br#"<?xml version="1.0"?>
      <MA xmlns="http://schemas.malighting.de/grandma2/xml/MA">
        <FixtureType name="Imported wash" mode="RGB">
          <manufacturer>Example</manufacturer>
          <Modules>
            <Module index="0" name="Main" class="Conventional" beamtype="Wash">
              <ChannelType attribute="DIM" feature="DIMMER" coarse="1">
                <ChannelFunction subattribute="DIM" attribute="DIM" feature="DIMMER" min_dmx_24="0" max_dmx_24="16777215"/>
              </ChannelType>
            </Module>
          </Modules>
        </FixtureType>
      </MA>"#;

    #[tokio::test]
    async fn snapshot_stream_closes_when_application_stops() {
        let directory = tempfile::tempdir().expect("temporary directory should be created");
        let app = Arc::new(
            App::load(directory.path().join("config.json"), true)
                .await
                .expect("simulated application should load"),
        );
        let api = GrpcApi::new(Arc::clone(&app));
        let response = api
            .watch_snapshots(Request::new(WatchSnapshotsRequest { interval_ms: 25 }))
            .await
            .expect("snapshot stream should start");
        let mut stream = response.into_inner();

        let initial = stream
            .next()
            .await
            .expect("snapshot stream should yield an initial item")
            .expect("initial snapshot should be valid");
        assert!(initial.snapshot.is_some());

        app.stop_runtime().await;

        let next = tokio::time::timeout(Duration::from_secs(1), stream.next())
            .await
            .expect("snapshot stream should stop promptly");
        assert!(next.is_none());
    }

    #[test]
    fn live_frame_keeps_current_signals_without_repeating_heavy_history() {
        let snapshot = ShowSnapshot {
            sequence: 42,
            captured_at_unix_ms: 1_234,
            audio: Some(AudioAnalysis {
                energy: 0.8,
                waveform: vec![0.25; 100],
                spectrum: vec![0.5; 32],
                spectrogram: vec![
                    SpectrogramFrame {
                        bins: vec![0.75; 64],
                    };
                    50
                ],
                history: vec![AnalysisHistoryFrame::default(); 50],
                onset_history: vec![0.5; 64],
                ..Default::default()
            }),
            ..Default::default()
        };

        let frame = live_frame(&snapshot);
        let audio = frame.audio.as_ref().expect("live audio should be present");

        assert_eq!(frame.sequence, snapshot.sequence);
        assert_eq!(audio.waveform.len(), 100);
        assert_eq!(audio.spectrum.len(), 32);
        assert!(
            frame.encoded_len() * 4 < snapshot.encoded_len(),
            "the high-rate live frame must stay substantially smaller than a history snapshot"
        );
    }

    #[tokio::test]
    async fn invalid_structured_config_returns_invalid_argument() {
        let directory = tempfile::tempdir().expect("temporary directory should be created");
        let app = Arc::new(
            App::load(directory.path().join("config.json"), true)
                .await
                .expect("simulated application should load"),
        );
        app.start_runtime()
            .await
            .expect("show runtime should start");
        let api = GrpcApi::new(Arc::clone(&app));
        let mut config = app.config().await;
        config.audio.as_mut().expect("audio configuration").mode = i32::MAX;

        let error = api
            .update_config(Request::new(UpdateConfigRequest {
                config: Some(config),
            }))
            .await
            .expect_err("invalid configuration should be rejected");

        assert_eq!(error.code(), tonic::Code::InvalidArgument);
        app.stop_runtime().await;
    }

    #[tokio::test]
    async fn imported_grandma2_fixture_is_persisted_and_listed() {
        let directory = tempfile::tempdir().expect("temporary directory should be created");
        let config_path = directory.path().join("config.json");
        let app = Arc::new(
            App::load(config_path.clone(), true)
                .await
                .expect("simulated application should load"),
        );
        app.start_runtime()
            .await
            .expect("show runtime should start");
        let api = GrpcApi::new(Arc::clone(&app));

        let response = api
            .import_grand_ma2_fixture(Request::new(ImportGrandMa2FixtureRequest {
                filename: "imported-wash.xml".into(),
                xml: IMPORTED_FIXTURE.into(),
            }))
            .await
            .expect("valid grandMA2 fixture should import")
            .into_inner();

        assert_eq!(response.fixture_types.len(), 1);
        assert_eq!(
            response.fixture_types[0].name, "Imported wash",
            "the API should return the parsed fixture type"
        );
        assert_eq!(
            response
                .config
                .expect("updated configuration should be returned")
                .imported_fixture_files
                .len(),
            1
        );

        let listed = api
            .list_grand_ma2_fixture_types(Request::new(ListGrandMa2FixtureTypesRequest {}))
            .await
            .expect("fixture library should be listed")
            .into_inner();
        assert_eq!(listed.fixture_types.len(), 4);
        assert!(
            listed
                .fixture_types
                .iter()
                .any(|fixture_type| fixture_type.name == "Imported wash")
        );

        let persisted =
            std::fs::read_to_string(config_path).expect("configuration should be persisted");
        assert!(persisted.contains("\"imported_fixture_files\""));
        assert!(persisted.contains("Imported wash"));
        app.stop_runtime().await;
    }
}
