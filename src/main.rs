use std::{net::SocketAddr, path::PathBuf, sync::Arc};

use anyhow::Context;
use axum::Router;
use clap::Parser;
use music_auto_show::{
    api::GrpcApi, app::App, assets,
    proto::v1::music_auto_show_service_server::MusicAutoShowServiceServer,
};
use tonic::service::Routes;
use tower_http::trace::TraceLayer;
use tracing::info;
use tracing_subscriber::EnvFilter;

#[derive(Debug, Parser)]
#[command(version, about)]
struct Cli {
    /// Address used by both the bundled SPA and gRPC-Web API.
    #[arg(long, default_value = "127.0.0.1:3000")]
    listen: SocketAddr,

    /// Load and save the show configuration at this path.
    #[arg(long, default_value = "config.json")]
    config: PathBuf,

    /// Use generated audio and an in-memory DMX interface.
    #[arg(long)]
    simulate: bool,
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(EnvFilter::try_from_default_env().unwrap_or_else(|_| "info".into()))
        .init();

    let cli = Cli::parse();
    let app_state = Arc::new(App::load(cli.config, cli.simulate).await?);
    app_state.start_runtime().await?;

    let grpc = MusicAutoShowServiceServer::new(GrpcApi::new(Arc::clone(&app_state)));
    let grpc_routes = Routes::new(grpc)
        .into_axum_router()
        .layer(tonic_web::GrpcWebLayer::new());
    let router = Router::new()
        .nest("/api", grpc_routes)
        .fallback(assets::serve)
        .layer(TraceLayer::new_for_http());

    let listener = tokio::net::TcpListener::bind(cli.listen)
        .await
        .with_context(|| format!("failed to bind {}", cli.listen))?;
    info!(address = %cli.listen, "Music Auto Show is ready");

    axum::serve(listener, router)
        .with_graceful_shutdown(shutdown_signal())
        .await?;
    app_state.stop_runtime().await;
    Ok(())
}

async fn shutdown_signal() {
    let ctrl_c = async {
        tokio::signal::ctrl_c()
            .await
            .expect("Ctrl-C handler installs");
    };

    #[cfg(unix)]
    let terminate = async {
        tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())
            .expect("SIGTERM handler installs")
            .recv()
            .await;
    };

    #[cfg(not(unix))]
    let terminate = std::future::pending::<()>();

    tokio::select! {
        () = ctrl_c => {},
        () = terminate => {},
    }
}
