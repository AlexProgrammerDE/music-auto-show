use std::time::{Duration, Instant};

/// A monotonic periodic schedule that preserves its phase across wake-up jitter.
///
/// Work that finishes late skips elapsed periods instead of running an unbounded
/// catch-up loop or shifting every future deadline by the wake-up delay.
#[derive(Debug, Clone, Copy)]
pub(crate) struct PeriodicSchedule {
    period: Duration,
    deadline: Instant,
}

impl PeriodicSchedule {
    pub(crate) fn immediate(period: Duration, now: Instant) -> Self {
        debug_assert!(!period.is_zero());
        Self {
            period,
            deadline: now,
        }
    }

    pub(crate) fn period(&self) -> Duration {
        self.period
    }

    pub(crate) fn remaining(&self, now: Instant) -> Duration {
        self.deadline.saturating_duration_since(now)
    }

    pub(crate) fn is_due(&self, now: Instant) -> bool {
        now >= self.deadline
    }

    /// Advances to the first deadline strictly after `now`.
    ///
    /// The return value is the number of elapsed deadlines that were skipped.
    pub(crate) fn advance(&mut self, now: Instant) -> u64 {
        let lateness = now.saturating_duration_since(self.deadline);
        let elapsed_periods = lateness.as_nanos() / self.period.as_nanos();
        let advance_periods = elapsed_periods.saturating_add(1);
        let Ok(advance_periods_u32) = u32::try_from(advance_periods) else {
            self.deadline = now + self.period;
            return u64::MAX;
        };
        self.deadline += self.period.saturating_mul(advance_periods_u32);
        u64::try_from(elapsed_periods).unwrap_or(u64::MAX)
    }

    pub(crate) fn reset(&mut self, period: Duration, now: Instant) {
        debug_assert!(!period.is_zero());
        self.period = period;
        self.deadline = now;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn wake_up_jitter_does_not_shift_the_cadence() {
        let started = Instant::now();
        let period = Duration::from_millis(25);
        let mut schedule = PeriodicSchedule::immediate(period, started);

        assert_eq!(schedule.advance(started + Duration::from_millis(1)), 0);
        assert_eq!(
            schedule.remaining(started + Duration::from_millis(2)),
            Duration::from_millis(23)
        );
    }

    #[test]
    fn overruns_skip_elapsed_deadlines() {
        let started = Instant::now();
        let period = Duration::from_millis(25);
        let mut schedule = PeriodicSchedule::immediate(period, started);

        assert_eq!(schedule.advance(started + Duration::from_millis(63)), 2);
        assert_eq!(
            schedule.remaining(started + Duration::from_millis(63)),
            Duration::from_millis(12)
        );
    }

    #[test]
    fn reset_applies_a_new_period_immediately() {
        let started = Instant::now();
        let mut schedule = PeriodicSchedule::immediate(Duration::from_secs(1), started);
        schedule.advance(started);

        schedule.reset(
            Duration::from_millis(25),
            started + Duration::from_millis(10),
        );

        assert!(schedule.is_due(started + Duration::from_millis(10)));
        assert_eq!(schedule.period(), Duration::from_millis(25));
    }
}
