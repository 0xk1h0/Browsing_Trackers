ACSAC 2026 Artifact
Brave New Browsing! Tracker Exposure under Browser-Agent Delegation
===================================================================

Kiho Lee, Chaejin Lim, Eunsoo Kim, Seyoung Jin, Beomjin Jin, and
Hyoungshick Kim. In Proceedings of the 42nd Annual Computer Security
Applications Conference (ACSAC 2026), Los Angeles, CA, USA.

Badges requested: Available, Functional, Reproduced.

Start here. Section 1 gets you a result in about five minutes. The rest
explains what you are looking at.


1. Run it
---------

    bash install.sh
    PYTHON=$PWD/.venv/bin/python bash artifact/REPRODUCE.sh
    .venv/bin/python artifact/verify.py

Expected output, verbatim, at the end of the second and third commands:

    [REPRODUCE] All modules reproduced successfully.
    [verify] All checks passed.

To check one paper claim instead of everything:

    bash install.sh
    bash claims/01_matched_exposure/run.sh

Nothing else to configure. No GPU, no proxy, no API key, and no network access
once the dependencies are installed. Timings and resource use are in
infrastructure/resources. If you would rather not install anything locally, the
Colab notebook and the Dockerfile in infrastructure/url do the same thing.


2. What the artifact is
-----------------------

The paper asks how much third-party tracking a user is exposed to when a
browser agent does a web task for them, compared with a human doing the same
task. This artifact holds three things:

  - the measurement instrument (capture proxy, fingerprinting shim, tracker
    classifier, per-session aggregator),
  - the released measurement data (per-session records for 9,002 agent
    sessions on WebVoyager, 4,200 on Online Mind2Web, and 220 human sessions),
  - the analysis that turns that data into the paper's tables and figures.
    artifact/CLAIMS.md maps each one, and names the four results that rest on
    data held back in the collection tree rather than released here.

It is both code and data.


3. The seven claims
-------------------

Each claims/ folder holds one paper claim, a runnable script, and the expected
output to compare against. Run any of them on its own.

    01_matched_exposure   agents contact more tracker hosts than humans on the
                          same task                                   Table 1
    02_family_reach       agents reach tracker families humans do not  Table 4
    03_dose_response      exposure grows with cross-site navigation    Sec. 7.1
    04_architecture       the action space drives it, not the model    Table 2
    05_ablation           removing the navigate primitive cuts leakage Table 6
    06_cross_benchmark    the ordering holds on a second benchmark     Table 7
    07_pipeline           the released instrument regenerates the
                          published per-session metrics from raw
                          captures                                     Sec. 4.2

Claim 07 is the one that answers "how do we know you collected this the way you
say you did". It unpacks the released raw human sessions, re-runs the released
classifier and aggregator over them, and compares the result with the published
per-session table.


4. Where to look
----------------

    README.txt        this file
    install.sh        environment setup; --full also installs the collection
                      pipeline, which evaluation does not need
    license.txt       MIT for our code. The tracker filter lists under
                      artifact/pipeline/classifier/tracker_lists/ keep their
                      own licenses; see LICENSES.md there before redistributing
    use.txt           what the artifact is and is not suitable for, and the one
                      path that touches your system
    infrastructure/   url, resources, allocation: where to run it and what it
                      costs
    claims/           the seven claims above
    artifact/         the artifact itself
        README.md     module-by-module documentation
        CLAIMS.md     every paper number mapped to the script and file that
                      produces it
        INSTALL.md    the full collection path, GPU and proxy included
        REPRODUCE.sh  regenerate everything
        verify.py     compare against reference values, non-zero exit on any
                      mismatch


5. What you cannot reproduce, and why
-------------------------------------

The original collection ran against the live web between 22 March and 13 April
2026 and used about 500 GPU-hours plus paid API calls. The web has changed
since, so re-running it cannot return the paper's numbers. We release that code
for inspection and for single-session demonstration, and claim 07 gives you a
way to check the instrument without it.

Raw HTTP bodies and raw cookie values are withheld under the study's IRB
protocol. Cookie values survive only as salted hashes, and the salt is not
released. Four results are supported by the collection tree rather than by this
release, and CLAIMS.md marks each one: the Table 8 classifier validation, the
cookie-set re-identifiability figure, the 78.3% consent-accept rate, and the
sham-banner control. Everything else in the paper is derivable from what is
released.


6. Where the artifact and the accepted PDF differ
-------------------------------------------------

The accepted PDF is the version the committee reviewed. The camera-ready
applies corrections the authors committed to in the author response, plus three
found while preparing this artifact. The artifact shows the corrected value
everywhere. artifact/CLAIMS.md lists each one with the old value, the new one,
and the file that supports it.

The two you are most likely to notice:

  - Table 2, SoM-GLM and UI-TARS rows. The other five rows reproduce cell for
    cell across all eleven columns. These two do not. The SoM-GLM pair is not
    derivable from any collection run of that agent, and the UI-TARS row came
    from a longer second run than the one the caption's pool size and this
    artifact use.

  - Table 6, two Browser-Use medians. The shipped data gives 346 where the
    paper prints 227 for the default condition, and 285 where it prints 185 for
    the negative control. The other eight cells of that table reproduce exactly,
    and the correction makes the reported reduction larger rather than smaller.

We would rather you read this here than discover it.


7. Contact and citation
-----------------------

One author watches the HotCRP artifact submission for the whole evaluation
period and answers within one working day.

To cite the paper or this artifact, use CITATION.cff at the repository root,
or:

    @inproceedings{lee2026bravenewbrowsing,
      title     = {Brave New Browsing! Tracker Exposure under Browser-Agent
                   Delegation},
      author    = {Lee, Kiho and Lim, Chaejin and Kim, Eunsoo and Jin, Seyoung
                   and Jin, Beomjin and Kim, Hyoungshick},
      booktitle = {Proceedings of the 42nd Annual Computer Security
                   Applications Conference (ACSAC)},
      address   = {Los Angeles, CA, USA},
      year      = {2026}
    }
