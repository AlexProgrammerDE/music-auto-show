use std::{fmt, io};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ShutdownEvent {
    Interrupt,
    Terminate,
    Quit,
    Hangup,
    #[cfg(windows)]
    ConsoleClose,
    #[cfg(windows)]
    ConsoleLogoff,
    #[cfg(windows)]
    SystemShutdown,
}

impl fmt::Display for ShutdownEvent {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        let name = match self {
            Self::Interrupt => "interrupt",
            Self::Terminate => "termination request",
            Self::Quit => "quit request",
            Self::Hangup => "terminal hangup",
            #[cfg(windows)]
            Self::ConsoleClose => "console close",
            #[cfg(windows)]
            Self::ConsoleLogoff => "console logoff",
            #[cfg(windows)]
            Self::SystemShutdown => "system shutdown",
        };
        formatter.write_str(name)
    }
}

#[cfg(unix)]
pub struct ShutdownSignals {
    interrupt: tokio::signal::unix::Signal,
    terminate: tokio::signal::unix::Signal,
    quit: tokio::signal::unix::Signal,
    hangup: tokio::signal::unix::Signal,
}

#[cfg(unix)]
impl ShutdownSignals {
    pub fn new() -> io::Result<Self> {
        use tokio::signal::unix::{SignalKind, signal};

        Ok(Self {
            interrupt: signal(SignalKind::interrupt())?,
            terminate: signal(SignalKind::terminate())?,
            quit: signal(SignalKind::quit())?,
            hangup: signal(SignalKind::hangup())?,
        })
    }

    pub async fn recv(&mut self) -> io::Result<ShutdownEvent> {
        tokio::select! {
            event = self.interrupt.recv() => event.map(|()| ShutdownEvent::Interrupt),
            event = self.terminate.recv() => event.map(|()| ShutdownEvent::Terminate),
            event = self.quit.recv() => event.map(|()| ShutdownEvent::Quit),
            event = self.hangup.recv() => event.map(|()| ShutdownEvent::Hangup),
        }
        .ok_or_else(signal_stream_closed)
    }
}

#[cfg(windows)]
pub struct ShutdownSignals {
    interrupt: tokio::signal::windows::CtrlC,
    quit: tokio::signal::windows::CtrlBreak,
    close: tokio::signal::windows::CtrlClose,
    logoff: tokio::signal::windows::CtrlLogoff,
    shutdown: tokio::signal::windows::CtrlShutdown,
}

#[cfg(windows)]
impl ShutdownSignals {
    pub fn new() -> io::Result<Self> {
        use tokio::signal::windows;

        Ok(Self {
            interrupt: windows::ctrl_c()?,
            quit: windows::ctrl_break()?,
            close: windows::ctrl_close()?,
            logoff: windows::ctrl_logoff()?,
            shutdown: windows::ctrl_shutdown()?,
        })
    }

    pub async fn recv(&mut self) -> io::Result<ShutdownEvent> {
        tokio::select! {
            event = self.interrupt.recv() => event.map(|()| ShutdownEvent::Interrupt),
            event = self.quit.recv() => event.map(|()| ShutdownEvent::Quit),
            event = self.close.recv() => event.map(|()| ShutdownEvent::ConsoleClose),
            event = self.logoff.recv() => event.map(|()| ShutdownEvent::ConsoleLogoff),
            event = self.shutdown.recv() => event.map(|()| ShutdownEvent::SystemShutdown),
        }
        .ok_or_else(signal_stream_closed)
    }
}

#[cfg(not(any(unix, windows)))]
pub struct ShutdownSignals;

#[cfg(not(any(unix, windows)))]
impl ShutdownSignals {
    pub fn new() -> io::Result<Self> {
        Ok(Self)
    }

    pub async fn recv(&mut self) -> io::Result<ShutdownEvent> {
        tokio::signal::ctrl_c().await?;
        Ok(ShutdownEvent::Interrupt)
    }
}

fn signal_stream_closed() -> io::Error {
    io::Error::new(io::ErrorKind::BrokenPipe, "shutdown signal stream closed")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn shutdown_events_have_human_readable_names() {
        assert_eq!(ShutdownEvent::Interrupt.to_string(), "interrupt");
        assert_eq!(ShutdownEvent::Terminate.to_string(), "termination request");
        assert_eq!(ShutdownEvent::Quit.to_string(), "quit request");
        assert_eq!(ShutdownEvent::Hangup.to_string(), "terminal hangup");
    }
}
