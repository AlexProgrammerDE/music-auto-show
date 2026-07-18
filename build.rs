use std::{env, process::Command};

fn main() {
    println!("cargo:rerun-if-changed=proto/music_auto_show/v1/music_auto_show.proto");
    println!("cargo:rerun-if-changed=frontend/src");
    println!("cargo:rerun-if-changed=frontend/index.html");
    println!("cargo:rerun-if-changed=frontend/package.json");
    println!("cargo:rerun-if-changed=frontend/bun.lock");
    println!("cargo:rerun-if-changed=frontend/vite.config.ts");

    let protoc = protoc_bin_vendored::protoc_bin_path().expect("vendored protoc is available");
    // SAFETY: build scripts run single-threaded before application code, and this
    // value is only consumed by prost-build in this process.
    unsafe { env::set_var("PROTOC", protoc) };

    tonic_prost_build::configure()
        .build_server(true)
        .build_client(false)
        .type_attribute(".", "#[derive(serde::Serialize, serde::Deserialize)]")
        .message_attribute(".", "#[serde(default, deny_unknown_fields)]")
        .enum_attribute(".", "#[serde(rename_all = \"snake_case\")]")
        .compile_protos(
            &["proto/music_auto_show/v1/music_auto_show.proto"],
            &["proto"],
        )
        .expect("protobuf definitions compile");

    let status = Command::new("bun")
        .args(["run", "--cwd", "frontend", "build"])
        .status()
        .expect("Bun is required to build the bundled SPA");
    assert!(status.success(), "Vite SPA build failed");
}
