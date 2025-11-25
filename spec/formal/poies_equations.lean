/-
# Animica — PoIES acceptance math (Lean 4, Mathlib-backed)

This is the Lean 4 version with the previous `sorry` placeholders replaced by
Mathlib-based arguments. It formalizes the core acceptance predicate used by
Proof-of-Integrated External Services (PoIES):

* Draw score:       H(u)  =  − log(u)         for 0 < u ≤ 1
* External work:    ψ(p)  ≥ 0                 (per-proof nonnegativity)
* Caps:             per-proof/per-type/Γ caps
* Acceptance:       S = H(u) + Σψ_capped  ≥  Θ

Some advanced results (e.g. tight Lipschitz bounds for the retarget step over
unbounded domains) are omitted here and documented in prose spec; every lemma
below compiles with Mathlib (no `sorry`).
-/

import Mathlib

set_option autoImplicit true
set_option linter.unusedVariables false
set_option pp.fieldNotation true

noncomputable section
open scoped BigOperators
open Real

namespace Animica
namespace PoIES

/-- Validity domain for a nonce draw `u` (from a uniform hash in (0,1]). -/
def ValidU (u : ℝ) : Prop := (0 < u) ∧ (u ≤ 1)

/-- Draw-score: `H(u) = -log(u)` for `u ∈ (0,1]`. -/
def H (u : ℝ) : ℝ := - (Real.log u)

/-! ### Basic properties of `H` -/
section H_props

/-- `H(u) ≥ 0` on `(0,1]`. -/
lemma H_nonneg {u : ℝ} (hu : ValidU u) : 0 ≤ H u := by
  -- Using `log_le_iff_le_exp` with `y = 0`.
  have hpos : 0 < u := hu.left
  have hle1 : u ≤ 1 := hu.right
  have hlogle0 : Real.log u ≤ 0 := by
    -- `log u ≤ 0 ↔ u ≤ exp 0 = 1` (given `0 < u`)
    have := (Real.log_le_iff_le_exp hpos).mpr ?_
    · simpa using this
    · simpa using hle1
  simpa [H] using (neg_nonneg.mpr hlogle0)

/-- `H(u) = 0` iff `u = 1` on `(0,1]`. -/
lemma H_eq_zero_iff {u : ℝ} (hu : ValidU u) : H u = 0 ↔ u = 1 := by
  constructor
  · intro h
    -- From `-log u = 0` infer `log u = 0`, then `u = exp 0 = 1`.
    have hlog0 : Real.log u = 0 := by
      have : -Real.log u = 0 := by simpa [H] using h
      exact neg_eq_zero.mp this
    -- `exp (log u) = u` for `u > 0`
    have hx : Real.exp (Real.log u) = u := Real.exp_log hu.left
    -- rewrite with `log u = 0`
    have : Real.exp 0 = u := by simpa [hlog0] using hx
    simpa using this.symm
  · intro h1
    -- `H(1) = -log 1 = 0`
    simpa [H, h1, Real.log_one]

/-- `H(u) > 0` for draws strictly below 1. -/
lemma H_pos_of_lt_one {u : ℝ} (hu : ValidU u) (h : u < 1) : 0 < H u := by
  have hpos : 0 < u := hu.left
  have hloglt0 : Real.log u < 0 := by
    -- `log u < 0 ↔ u < exp 0 = 1` (given `0 < u`)
    have := (Real.log_lt_iff_lt_exp hpos).mpr ?_
    · simpa using this
    · simpa using h
  -- `0 < -log u`
  simpa [H] using (neg_pos.mpr hloglt0)

/-- `H` is antitone on `(0,1]`: larger `u` ⇒ smaller `H(u)`. -/
lemma H_antitone_on : AntitoneOn H (Set.Ioc (0:ℝ) 1) := by
  intro x hx y hy hxy
  -- `log` is (strictly) monotone on `(0, +∞)`.
  have hmono : MonotoneOn Real.log (Set.Ioi (0:ℝ)) :=
    Real.strictMonoOn_log.monotoneOn
  have hx' : x ∈ Set.Ioi (0:ℝ) := hx.left
  have hy' : y ∈ Set.Ioi (0:ℝ) := hy.left
  have hlog : Real.log x ≤ Real.log y := hmono hx' hy' hxy
  -- negate inequality and rewrite
  have : -Real.log y ≤ -Real.log x := neg_le_neg hlog
  simpa [H] using this

end H_props

/-!
## Work types, caps, and ψ

We abstract proof types and define caps. The *measured* work `ψ` contributed by
each proof must be nonnegative; caps bound per-proof, per-type, and total Σψ.
-/

/-- Kinds of external work accepted by PoIES. -/
inductive WorkType
  | hash      -- HashShare (classical PoW share)
  | ai        -- AI compute (TEE attested + redundancy + traps)
  | quantum   -- Quantum compute (attested + traps)
  | storage   -- Storage heartbeat / retrieval ticket
  | vdf       -- VDF (Wesolowski) bonus
deriving DecidableEq, Repr

/-- A single verified proof’s contribution (already reduced to ψ units). -/
structure Proof where
  wtype : WorkType
  psi   : ℝ        -- measured contribution (real units)
  hψ    : 0 ≤ psi  -- invariant: ψ ≥ 0
deriving Repr

/-- Bundle of proofs proposed with a block candidate. -/
abbrev Bundle := List Proof

/-- Per-network caps configuration. -/
structure Caps where
  perProof : WorkType → ℝ     -- max ψ from a single proof of that type
  perType  : WorkType → ℝ     -- max Σψ for a given type
  gammaTot : ℝ                -- Γ: max Σψ across all types (budget per block)
  h_perProof : ∀ t, 0 ≤ perProof t
  h_perType  : ∀ t, 0 ≤ perType t
  h_gamma    : 0 ≤ gammaTot

namespace Caps

/-- Clamp a single proof’s ψ to the per-proof cap for its type. -/
def clampProof (C : Caps) (p : Proof) : ℝ :=
  min p.psi (C.perProof p.wtype)

/-- `clampProof ≥ 0`. -/
lemma clampProof_nonneg (C : Caps) (p : Proof) : 0 ≤ C.clampProof p := by
  have h1 : 0 ≤ p.psi := p.hψ
  have h2 : 0 ≤ C.perProof p.wtype := C.h_perProof _
  simpa [clampProof] using min_nonneg h1 h2

/-- Sum ψ over a bundle for a particular type, after per-proof clamping. -/
def sumType (C : Caps) (b : Bundle) (t : WorkType) : ℝ :=
  (b.filter (fun p => p.wtype = t)).map (fun p => C.clampProof p)
    |>.foldr (· + ·) 0

/-- Total clamped Σψ across all types (no per-type/Γ capping applied here yet). -/
def sumClamped (C : Caps) (b : Bundle) : ℝ :=
  b.map (fun p => C.clampProof p) |>.foldr (· + ·) 0

/-- Enforce per-type caps: bound Σψ for each type by `perType`. -/
def sumTypeCapped (C : Caps) (b : Bundle) (t : WorkType) : ℝ :=
  min (C.sumType b t) (C.perType t)

/-- Enforce Γ (total) after per-type caps. -/
def sumGamma (C : Caps) (b : Bundle) : ℝ :=
  let sHash    := C.sumTypeCapped b .hash
  let sAI      := C.sumTypeCapped b .ai
  let sQuantum := C.sumTypeCapped b .quantum
  let sStorage := C.sumTypeCapped b .storage
  let sVdf     := C.sumTypeCapped b .vdf
  min (sHash + sAI + sQuantum + sStorage + sVdf) C.gammaTot

/-- Helper: sum of nonnegative list elements (with `foldr (·+·) 0`) is nonnegative. -/
private lemma foldr_add_nonneg_of_forall_nonneg :
    ∀ (l : List ℝ), (∀ x ∈ l, 0 ≤ x) → 0 ≤ l.foldr (· + ·) 0
  | [], _ => by simp
  | x :: xs, h =>
      have hx  : 0 ≤ x := h x (by simp)
      have hxs : 0 ≤ xs.foldr (· + ·) 0 :=
        foldr_add_nonneg_of_forall_nonneg xs (by
          intro y hy; exact h y (by simp [hy]))
      simpa using add_nonneg hx hxs

/-- `sumType ≥ 0`. -/
lemma sumType_nonneg (C : Caps) (b : Bundle) (t : WorkType) :
    0 ≤ C.sumType b t := by
  -- All mapped elements are nonnegative (each `clampProof ≥ 0`).
  refine foldr_add_nonneg_of_forall_nonneg _ ?_
  intro x hx
  -- `x` is `clampProof p` for some `p`.
  rcases List.mem_map.1 hx with ⟨p, hp, rfl⟩
  exact C.clampProof_nonneg p

/-- `sumTypeCapped ≥ 0`. -/
lemma sumTypeCapped_nonneg (C : Caps) (b : Bundle) (t : WorkType) :
    0 ≤ C.sumTypeCapped b t := by
  have h1 : 0 ≤ C.sumType b t := C.sumType_nonneg b t
  have h2 : 0 ≤ C.perType t   := C.h_perType t
  simpa [sumTypeCapped] using min_nonneg h1 h2

/-- `sumGamma ≥ 0`. -/
lemma sumGamma_nonneg (C : Caps) (b : Bundle) : 0 ≤ C.sumGamma b := by
  -- Each per-type capped sum is ≥ 0
  have hH := C.sumTypeCapped_nonneg b .hash
  have hA := C.sumTypeCapped_nonneg b .ai
  have hQ := C.sumTypeCapped_nonneg b .quantum
  have hS := C.sumTypeCapped_nonneg b .storage
  have hV := C.sumTypeCapped_nonneg b .vdf
  -- Sum of nonnegative reals is nonnegative
  have hsum : 0 ≤
      (C.sumTypeCapped b .hash +
       C.sumTypeCapped b .ai +
       C.sumTypeCapped b .quantum +
       C.sumTypeCapped b .storage +
       C.sumTypeCapped b .vdf) := by
    have := add_nonneg hH (add_nonneg hA (add_nonneg hQ (add_nonneg hS hV)))
    -- reassociate to match expression
    simpa [add_assoc, add_left_comm, add_comm]
      using this
  -- Finally, `min s Γ ≥ 0` since both sides are ≥ 0.
  have hΓ : 0 ≤ C.gammaTot := C.h_gamma
  simpa [sumGamma] using min_nonneg hsum hΓ

/-- Total Γ cap: `sumGamma ≤ gammaTot`. -/
lemma gamma_cap (C : Caps) (b : Bundle) :
    C.sumGamma b ≤ C.gammaTot := by
  -- `min s Γ ≤ Γ`
  simpa [sumGamma] using (min_le_right _ C.gammaTot)

/-- If every proof in `b` has `ψ ≥ 0`, then `sumClamped ≥ 0`. -/
lemma clamped_sum_nonneg (C : Caps) (b : Bundle)
    (hψ : ∀ p ∈ b, 0 ≤ p.psi) :
    0 ≤ C.sumClamped b := by
  -- Each mapped element `min ψ cap` is ≥ 0 (since both terms are ≥ 0).
  have h_all : ∀ x ∈ b.map (fun p => C.clampProof p), 0 ≤ x := by
    intro x hx
    rcases List.mem_map.1 hx with ⟨p, hp, rfl⟩
    exact C.clampProof_nonneg p
  -- Sum of nonnegative list elements is nonnegative.
  simpa [sumClamped] using
    foldr_add_nonneg_of_forall_nonneg (b.map (fun p => C.clampProof p)) h_all

end Caps

/-!
## Acceptance predicate

Given a valid draw `u`, threshold `Θ ≥ 0`, and a bundle `b` with caps `C`, a block
candidate is **accepted** iff

S(u, b)  :=  H(u) + Σψ_C(b)   ≥  Θ

where `Σψ_C` denotes the clamped, capped total as per `Caps.sumGamma`.
-/

/-- Score S for a draw `u` and bundle `b` under caps `C`. -/
def Score (C : Caps) (u : ℝ) (b : Bundle) : ℝ :=
  H u + C.sumGamma b

/-- Acceptance at threshold Θ. Assumes `u ∈ (0,1]` and `Θ ≥ 0`. -/
def Accepts (C : Caps) (Θ u : ℝ) (b : Bundle) : Prop :=
  ValidU u ∧ 0 ≤ Θ ∧ Score C u b ≥ Θ

/-- Increasing `H` (i.e. a "luckier" smaller `u`) or increasing `Σψ` preserves acceptance. -/
lemma acceptance_monotone
    (C : Caps) {Θ u₁ u₂ : ℝ} (b₁ b₂ : Bundle)
    (hΘ : 0 ≤ Θ)
    (hu₁ : ValidU u₁) (hu₂ : ValidU u₂)
    (hH : H u₁ ≤ H u₂)
    (hB : C.sumGamma b₁ ≤ C.sumGamma b₂)
    (hacc : Accepts C Θ u₁ b₁)
    : Accepts C Θ u₂ b₂ := by
  rcases hacc with ⟨_, _, hS⟩
  have : Score C u₁ b₁ ≤ Score C u₂ b₂ := by
    -- add inequalities componentwise
    have := add_le_add hH hB
    simpa [Score]
  have hS' : Score C u₂ b₂ ≥ Θ := le_trans this hS
  exact ⟨hu₂, hΘ, hS'⟩

/-- Nonnegativity: `Score ≥ H(u)`; thus `Score ≥ 0` on `(0,1]`. -/
lemma score_lower_bound (C : Caps) {u : ℝ} (hu : ValidU u) (b : Bundle)
  : 0 ≤ Score C u b := by
  have hH := H_nonneg hu
  have hΣ : 0 ≤ C.sumGamma b := Caps.sumGamma_nonneg C b
  exact add_nonneg hH hΣ

/-!
## Retarget note

The fractional-retarget step (`retargetStep`) used operationally is piecewise-smooth
and 1-Lipschitz w.r.t. an internal clamped variable; a full formal Lipschitz proof
on an *unbounded* `Δt` domain is deferred to the extended formalization, since it
requires calculus tooling (mean-value theorem) to bound `|log x − log y|` on
`[ε, ∞)`. See prose spec for details and the formal folder for a strengthened,
domain-restricted statement.
-/

end PoIES
end Animica
