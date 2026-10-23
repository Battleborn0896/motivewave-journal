package org.mwjournal.study;

/** The study has nothing to set. A trader adds it to the chart they trade, the contract's own or a continuous
 * one, and it works with these fixed values. Its dialog is one line (NOTE).
 *
 * Fixed besides the numbers below: the bridge file is the default one (MotiveWaveJournal.defaultFile); prices
 * are written under the chart's active contract; fills are always read; prints are recorded during trades only
 * (JournalTicks); replay prints are recorded, as real ticks; history ends at the end of the chart, and no ticks
 * are loaded when a chart loads; only the history a continuous chart loads is marked proxy (JournalPriceEvents).
 *
 * Studies up to 0.3.9 had twelve settings, and MotiveWave keeps their saved values in every chart that had the
 * study. Nothing reads them any more, so none of them acts.
 *
 * Pure: no MotiveWave types. MotiveWaveJournal.describe turns the dialog into descriptors. */
final class JournalSettings {
  static final String TAB = "General", GROUP = "MotiveWave Journal";
  static final String NOTE = "Add the study to the chart you trade. There is nothing to set.";

  /** Days of one-minute bars written when a chart loads, and kept up to date after. In replay: those days
   * before the replay's time and the day after it, whole. */
  static final int BAR_DAYS = 3;
  /** Closed trades of the last so many days get the real ticks of their holding time from MotiveWave's tick
   * history (JournalBackfill). In replay, counted back from the replay's time. */
  static final int BACKFILL_DAYS = 30;
  /** Minutes after each exit loaded from the same tick history, for the runner analysis. */
  static final int AFTER_EXIT_MINUTES = 30;

  private JournalSettings() {}
}
