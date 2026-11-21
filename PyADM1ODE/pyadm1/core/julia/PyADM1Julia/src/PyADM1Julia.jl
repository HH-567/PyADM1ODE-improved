# PyADM1Julia - ADM1da right-hand side as a precompilable package.
#
# Generated from pyadm1/core/julia/adm1da.jl by gen_package.py. Edit the
# source there and re-run the generator; do not edit this copy.

module PyADM1Julia

using OrdinaryDiffEqRosenbrock: Rodas4P
using SciMLBase: SciMLBase, ODEProblem, solve
using PrecompileTools: @compile_workload

const N_STATE = 41
const N_INFLUENT = 37

# Field order is injected by the Python bridge (single source of truth).
include("fields.jl")
include("workload_data.jl")
const NP = length(PFIELDS)

@inline function unpack(p::Vector{Float64})
    return NamedTuple{PFIELDS}(Tuple(p[1:NP]))
end

# ---------------------------------------------------------------------------
# Helpers — direct ports of ADM1._pH_inhib and ADM1._calc_ph
# ---------------------------------------------------------------------------

# Deliberately untyped: SciML differentiates the RHS with ForwardDiff, which
# calls these with Dual numbers. Annotating ::Float64 would make the Jacobian
# fall back to "automatic differentiation failed".
@inline function ph_inhib(S_H, K_pH, n::Int)
    Kn = K_pH^n
    return Kn / (Kn + S_H^n)
end

@inline function calc_ph(
    S_nh4,
    S_nh3,
    S_hco3,
    S_ac_ion,
    S_pro_ion,
    S_bu_ion,
    S_va_ion,
    S_cation,
    S_anion,
    K_w,
    max_iter::Int = 50,
)
    vfa_anions = S_ac_ion / 64.0 + S_pro_ion / 112.0 + S_bu_ion / 160.0 + S_va_ion / 208.0
    fixed = S_cation - S_anion + (S_nh4 - S_nh3) - S_hco3 - vfa_anions

    S_H = 1.0e-7
    @inbounds for _ in 1:max_iter
        f = fixed + S_H - K_w / (S_H + 1.0e-30)
        df = 1.0 + K_w / (S_H + 1.0e-30)^2
        delta = -f / df
        S_H = max(1.0e-14, S_H + delta)
        if abs(delta) < 1.0e-15
            break
        end
    end
    return S_H
end

# Hydrolysis rates, shared by the RHS and the sludge-volume balance so the two
# can never disagree about what q_S_loss is.
@inline function hydrolysis_rates(u, pa)
    Rho_hyd_ch = pa.k_hyd_ch * @inbounds(u[19])
    Rho_hyd_pr = pa.k_hyd_pr * @inbounds(u[20])
    Rho_hyd_li = pa.k_hyd_li * @inbounds(u[21])
    return (Rho_hyd_ch, Rho_hyd_pr, Rho_hyd_li)
end

@inline function q_s_loss_of(u, pa)
    h1, h2, h3 = hydrolysis_rates(u, pa)
    return pa.V_liq * (h1 * (0.9375 / 1550.0) + h2 * (0.6125 / 1370.0) + h3 * (0.3474 / 920.0))
end

# ---------------------------------------------------------------------------
# Right-hand side
# ---------------------------------------------------------------------------

function adm1da_rhs!(du, u, pa, s_in, t)
    @inbounds begin
        S_su = u[1]
        S_aa = u[2]
        S_fa = u[3]
        S_va = u[4]
        S_bu = u[5]
        S_pro = u[6]
        S_ac = u[7]
        S_h2 = u[8]
        S_ch4 = u[9]
        S_co2 = u[10]
        S_nh4 = u[11]
        S_I = u[12]

        X_PS_ch = u[13]
        X_PS_pr = u[14]
        X_PS_li = u[15]
        X_PF_ch = u[16]
        X_PF_pr = u[17]
        X_PF_li = u[18]
        X_S_ch = u[19]
        X_S_pr = u[20]
        X_S_li = u[21]
        X_I = u[22]

        X_su = u[23]
        X_aa = u[24]
        X_fa = u[25]
        X_c4 = u[26]
        X_pro = u[27]
        X_ac = u[28]
        X_h2 = u[29]

        S_cation = u[30]
        S_anion = u[31]
        S_va_ion = u[32]
        S_bu_ion = u[33]
        S_pro_ion = u[34]
        S_ac_ion = u[35]
        S_hco3 = u[36]
        S_nh3 = u[37]

        p_gas_h2 = u[38]
        p_gas_ch4 = u[39]
        p_gas_co2 = u[40]
        pTOTAL = u[41]

        # ---- Influent and hydraulic flow --------------------------------
        q_ad = pa.q_ad

        # ---- Local parameter aliases ------------------------------------
        Y_su = pa.Y_su
        Y_aa = pa.Y_aa
        Y_fa = pa.Y_fa
        Y_c4 = pa.Y_c4
        Y_pro = pa.Y_pro
        Y_ac = pa.Y_ac
        Y_h2 = pa.Y_h2

        f_fa_li = pa.f_fa_li
        f_ch_bac = pa.f_ch_bac
        f_pr_bac = pa.f_pr_bac
        f_li_bac = pa.f_li_bac
        f_p_bac = pa.f_p_bac
        fXI_PS = pa.fXI_PS
        fXI_PF = pa.fXI_PF
        fSI = pa.fSI_hyd

        # ---- pH (Newton-Raphson charge balance) -------------------------
        S_H = calc_ph(
            S_nh4, S_nh3, S_hco3, S_ac_ion, S_pro_ion, S_bu_ion, S_va_ion, S_cation, S_anion, pa.K_w,
        )
        S_H = max(S_H, 1.0e-14)

        # ---- Inhibition factors -----------------------------------------
        S_IN = S_nh4 + S_nh3
        I_IN = S_IN / (pa.K_S_IN + S_IN + 1.0e-20)

        I_pH_aa = ph_inhib(S_H, pa.K_pH_aa, 1)
        I_pH_fa = ph_inhib(S_H, pa.K_pH_aa, 2)
        I_pH_c4 = ph_inhib(S_H, pa.K_pH_aa, 2)
        I_pH_pro = ph_inhib(S_H, pa.K_pH_aa, 2)
        I_pH_ac = ph_inhib(S_H, pa.K_pH_ac, 3)
        I_pH_h2 = ph_inhib(S_H, pa.K_pH_h2, 3)

        I_h2_fa = pa.K_I_h2_fa / (pa.K_I_h2_fa + S_h2)
        I_h2_c4 = pa.K_I_h2_c4 / (pa.K_I_h2_c4 + S_h2)
        I_h2_pro = pa.K_I_h2_pro / (pa.K_I_h2_pro + S_h2)

        K_nh3_ac_sq = pa.K_I_nh3^2
        K_nh3_pro_sq = pa.K_I_nh3_pro^2
        S_nh3_sq = S_nh3 * S_nh3
        I_nh3 = K_nh3_ac_sq / (K_nh3_ac_sq + S_nh3_sq)
        I_nh3_pro = K_nh3_pro_sq / (K_nh3_pro_sq + S_nh3_sq)

        K_co2_h2_sq = pa.K_S_co2_h2 * pa.K_S_co2_h2
        S_co2_sq = S_co2 * S_co2
        I_co2_h2 = S_co2_sq / (K_co2_h2_sq + S_co2_sq + 1.0e-30)

        S_HPr = (S_pro / 112.0) * S_H / (S_H + pa.K_a_pro + 1.0e-20)
        I_HPr = pa.K_IH_pro / (pa.K_IH_pro + S_HPr + 1.0e-20)

        S_HAc = (S_ac / 64.0) * S_H / (S_H + pa.K_a_ac + 1.0e-20)
        I_HAc = pa.K_IH_ac / (pa.K_IH_ac + S_HAc + 1.0e-20)

        I_su = I_pH_aa * I_IN
        I_aa = I_pH_aa * I_IN
        I_fa = I_pH_fa * I_IN * I_h2_fa
        I_c4 = I_pH_c4 * I_IN * I_h2_c4
        I_pro = I_pH_pro * I_IN * I_h2_pro * I_HPr * I_nh3_pro
        I_ac = I_pH_ac * I_IN * I_nh3 * I_HAc
        I_h2 = I_pH_h2 * I_IN * I_co2_h2

        # ---- Process rates ----------------------------------------------
        Rho_dis_PS_ch = pa.k_dis_PS * X_PS_ch
        Rho_dis_PS_pr = pa.k_dis_PS * X_PS_pr
        Rho_dis_PS_li = pa.k_dis_PS * X_PS_li
        Rho_dis_PF_ch = pa.k_dis_PF * X_PF_ch
        Rho_dis_PF_pr = pa.k_dis_PF * X_PF_pr
        Rho_dis_PF_li = pa.k_dis_PF * X_PF_li

        Rho_hyd_ch = pa.k_hyd_ch * X_S_ch
        Rho_hyd_pr = pa.k_hyd_pr * X_S_pr
        Rho_hyd_li = pa.k_hyd_li * X_S_li

        Rho_su = pa.k_m_su * S_su / (pa.K_S_su + S_su + 1.0e-20) * X_su * I_su
        Rho_aa = pa.k_m_aa * S_aa / (pa.K_S_aa + S_aa + 1.0e-20) * X_aa * I_aa
        Rho_fa = pa.k_m_fa * S_fa / (pa.K_S_fa + S_fa + 1.0e-20) * X_fa * I_fa

        S_vbu = S_va + S_bu + 1.0e-20
        Rho_c4_va = pa.k_m_c4 * S_va / (pa.K_S_c4 + S_va + 1.0e-20) * X_c4 * (S_va / S_vbu) * I_c4
        Rho_c4_bu = pa.k_m_c4 * S_bu / (pa.K_S_c4 + S_bu + 1.0e-20) * X_c4 * (S_bu / S_vbu) * I_c4

        Rho_pro = pa.k_m_pro * S_pro / (pa.K_S_pro + S_pro + 1.0e-20) * X_pro * I_pro
        Rho_ac = pa.k_m_ac * S_ac / (pa.K_S_ac + S_ac + 1.0e-20) * X_ac * I_ac
        Rho_h2 = pa.k_m_h2 * S_h2 / (pa.K_S_h2 + S_h2 + 1.0e-20) * X_h2 * I_h2

        Rho_dec_su = pa.k_dec_su * X_su
        Rho_dec_aa = pa.k_dec_aa * X_aa
        Rho_dec_fa = pa.k_dec_fa * X_fa
        Rho_dec_c4 = pa.k_dec_c4 * X_c4
        Rho_dec_pro = pa.k_dec_pro * X_pro
        Rho_dec_ac = pa.k_dec_ac * X_ac
        Rho_dec_h2 = pa.k_dec_h2 * X_h2
        sum_decay =
            Rho_dec_su + Rho_dec_aa + Rho_dec_fa + Rho_dec_c4 + Rho_dec_pro + Rho_dec_ac + Rho_dec_h2

        # ---- Acid-base rates --------------------------------------------
        k_AB = pa.k_A_B
        Rho_A_va = k_AB * (S_va_ion * S_H - pa.K_a_va * (S_va - S_va_ion))
        Rho_A_bu = k_AB * (S_bu_ion * S_H - pa.K_a_bu * (S_bu - S_bu_ion))
        Rho_A_pro = k_AB * (S_pro_ion * S_H - pa.K_a_pro * (S_pro - S_pro_ion))
        Rho_A_ac = k_AB * (S_ac_ion * S_H - pa.K_a_ac * (S_ac - S_ac_ion))
        Rho_A_co2 = k_AB * (S_hco3 * S_H - pa.K_a_co2 * (S_co2 - S_hco3))
        Rho_A_IN = k_AB * (S_nh3 * S_H - pa.K_a_IN * (S_nh4 - S_nh3))

        # ---- Gas transfer rates -----------------------------------------
        k_L_a = pa.k_L_a
        Rho_T_h2 =
            k_L_a * (S_h2 - 16.0 * p_gas_h2 / (pa.RT * pa.K_H_h2)) * (pa.V_liq / pa.V_gas)
        Rho_T_ch4 =
            k_L_a * (S_ch4 - 64.0 * p_gas_ch4 / (pa.RT * pa.K_H_ch4)) * (pa.V_liq / pa.V_gas)
        S_co2_free = max(S_co2 - S_hco3, 0.0)
        Rho_T_co2 =
            k_L_a * (S_co2_free - p_gas_co2 / (pa.RT * pa.K_H_co2)) * (pa.V_liq / pa.V_gas)

        k_p = pa.k_p
        Rho_T_11 = max(k_p * (pTOTAL + pa.p_gas_h2o - pa.p_ext) * (pa.V_liq / pa.V_gas), 0.0)

        # ---- Carbon stoichiometry coefficients for S_co2 balance --------
        s_hyd_ch = -pa.C_ch + (1.0 - fSI) * pa.C_su + fSI * pa.C_I_s
        s_hyd_pr = -pa.C_pr + (1.0 - fSI) * pa.C_aa + fSI * pa.C_I_s
        s_hyd_li =
            -pa.C_li +
            (1.0 - fSI) * (f_fa_li * pa.C_fa + (1.0 - f_fa_li) * pa.C_su) +
            fSI * pa.C_I_s

        s_su =
            -pa.C_su +
            (1.0 - Y_su) * (pa.f_bu_su * pa.C_bu + pa.f_pro_su * pa.C_pro + pa.f_ac_su * pa.C_ac) +
            Y_su * pa.C_bac
        s_aa =
            -pa.C_aa +
            (1.0 - Y_aa) * (
                pa.f_va_aa * pa.C_va +
                pa.f_bu_aa * pa.C_bu +
                pa.f_pro_aa * pa.C_pro +
                pa.f_ac_aa * pa.C_ac
            ) +
            Y_aa * pa.C_bac
        s_fa = -pa.C_fa + (1.0 - Y_fa) * 0.7 * pa.C_ac + Y_fa * pa.C_bac
        s_c4_va = -pa.C_va + (1.0 - Y_c4) * (0.54 * pa.C_pro + 0.31 * pa.C_ac) + Y_c4 * pa.C_bac
        s_c4_bu = -pa.C_bu + (1.0 - Y_c4) * 0.8 * pa.C_ac + Y_c4 * pa.C_bac
        s_pro = -pa.C_pro + (1.0 - Y_pro) * 0.57 * pa.C_ac + Y_pro * pa.C_bac
        s_ac = -pa.C_ac + (1.0 - Y_ac) * pa.C_ch4 + Y_ac * pa.C_bac
        s_h2 = (1.0 - Y_h2) * pa.C_ch4 + Y_h2 * pa.C_bac
        s_dec = -pa.C_bac + f_ch_bac * pa.C_ch + f_pr_bac * pa.C_pr + f_li_bac * pa.C_li + f_p_bac * pa.C_I_x

        Sigma =
            s_hyd_ch * Rho_hyd_ch +
            s_hyd_pr * Rho_hyd_pr +
            s_hyd_li * Rho_hyd_li +
            s_su * Rho_su +
            s_aa * Rho_aa +
            s_fa * Rho_fa +
            s_c4_va * Rho_c4_va +
            s_c4_bu * Rho_c4_bu +
            s_pro * Rho_pro +
            s_ac * Rho_ac +
            s_h2 * Rho_h2 +
            s_dec * sum_decay

        # ---- Differential equations -------------------------------------
        D_in = q_ad / pa.V_liq

        _q_S_loss =
            pa.V_liq *
            (Rho_hyd_ch * (0.9375 / 1550.0) + Rho_hyd_pr * (0.6125 / 1370.0) + Rho_hyd_li * (0.3474 / 920.0))

        _Q_out = pa.has_q_out_override > 0.5 ? max(pa.q_out_override, 0.0) : max(q_ad - _q_S_loss, 0.0)
        D_out = _Q_out / pa.V_liq

        du[1] =
            D_in * s_in[1] - D_out * S_su +
            (1.0 - fSI) * Rho_hyd_ch +
            (1.0 - fSI) * (1.0 - f_fa_li) * Rho_hyd_li -
            Rho_su
        du[2] = D_in * s_in[2] - D_out * S_aa + (1.0 - fSI) * Rho_hyd_pr - Rho_aa
        du[3] =
            D_in * s_in[3] - D_out * S_fa + (1.0 - fSI) * f_fa_li * Rho_hyd_li - Rho_fa
        du[4] =
            D_in * s_in[4] - D_out * S_va + (1.0 - Y_aa) * pa.f_va_aa * Rho_aa - Rho_c4_va
        du[5] =
            D_in * s_in[5] - D_out * S_bu +
            (1.0 - Y_su) * pa.f_bu_su * Rho_su +
            (1.0 - Y_aa) * pa.f_bu_aa * Rho_aa -
            Rho_c4_bu
        du[6] =
            D_in * s_in[6] - D_out * S_pro +
            (1.0 - Y_su) * pa.f_pro_su * Rho_su +
            (1.0 - Y_aa) * pa.f_pro_aa * Rho_aa +
            (1.0 - Y_c4) * 0.54 * Rho_c4_va -
            Rho_pro
        du[7] =
            D_in * s_in[7] - D_out * S_ac +
            (1.0 - Y_su) * pa.f_ac_su * Rho_su +
            (1.0 - Y_aa) * pa.f_ac_aa * Rho_aa +
            (1.0 - Y_fa) * 0.7 * Rho_fa +
            (1.0 - Y_c4) * 0.31 * Rho_c4_va +
            (1.0 - Y_c4) * 0.8 * Rho_c4_bu +
            (1.0 - Y_pro) * 0.57 * Rho_pro -
            Rho_ac
        du[8] =
            D_in * s_in[8] - D_out * S_h2 +
            (1.0 - Y_su) * pa.f_h2_su * Rho_su +
            (1.0 - Y_aa) * pa.f_h2_aa * Rho_aa +
            (1.0 - Y_fa) * 0.3 * Rho_fa +
            (1.0 - Y_c4) * 0.15 * Rho_c4_va +
            (1.0 - Y_c4) * 0.2 * Rho_c4_bu +
            (1.0 - Y_pro) * 0.43 * Rho_pro -
            Rho_h2 -
            pa.V_gas / pa.V_liq * Rho_T_h2
        du[9] =
            D_in * s_in[9] - D_out * S_ch4 +
            (1.0 - Y_ac) * Rho_ac +
            (1.0 - Y_h2) * Rho_h2 -
            pa.V_gas / pa.V_liq * Rho_T_ch4
        du[10] =
            D_in * s_in[10] - D_out * S_co2 - Sigma - pa.V_gas / pa.V_liq * Rho_T_co2 + Rho_A_co2

        N_bac = pa.N_bac
        N_aa = pa.N_aa
        N_I = pa.N_I
        du[11] =
            D_in * s_in[11] - D_out * S_nh4 -
            Y_su * N_bac * Rho_su +
            (N_aa - Y_aa * N_bac) * Rho_aa -
            Y_fa * N_bac * Rho_fa -
            Y_c4 * N_bac * Rho_c4_va -
            Y_c4 * N_bac * Rho_c4_bu -
            Y_pro * N_bac * Rho_pro -
            Y_ac * N_bac * Rho_ac -
            Y_h2 * N_bac * Rho_h2 +
            (N_bac - f_pr_bac * N_aa - f_p_bac * N_I) * sum_decay +
            Rho_A_IN
        du[12] = D_in * s_in[12] - D_out * S_I + fSI * (Rho_hyd_ch + Rho_hyd_pr + Rho_hyd_li)

        du[13] = D_in * s_in[13] - D_out * X_PS_ch - Rho_dis_PS_ch
        du[14] = D_in * s_in[14] - D_out * X_PS_pr - Rho_dis_PS_pr
        du[15] = D_in * s_in[15] - D_out * X_PS_li - Rho_dis_PS_li
        du[16] = D_in * s_in[16] - D_out * X_PF_ch - Rho_dis_PF_ch
        du[17] = D_in * s_in[17] - D_out * X_PF_pr - Rho_dis_PF_pr
        du[18] = D_in * s_in[18] - D_out * X_PF_li - Rho_dis_PF_li
        du[19] =
            D_in * s_in[19] - D_out * X_S_ch +
            (1.0 - fXI_PS) * Rho_dis_PS_ch +
            (1.0 - fXI_PF) * Rho_dis_PF_ch +
            f_ch_bac * sum_decay -
            Rho_hyd_ch
        du[20] =
            D_in * s_in[20] - D_out * X_S_pr +
            (1.0 - fXI_PS) * Rho_dis_PS_pr +
            (1.0 - fXI_PF) * Rho_dis_PF_pr +
            f_pr_bac * sum_decay -
            Rho_hyd_pr
        du[21] =
            D_in * s_in[21] - D_out * X_S_li +
            (1.0 - fXI_PS) * Rho_dis_PS_li +
            (1.0 - fXI_PF) * Rho_dis_PF_li +
            f_li_bac * sum_decay -
            Rho_hyd_li
        du[22] =
            D_in * s_in[22] - D_out * X_I +
            fXI_PS * (Rho_dis_PS_ch + Rho_dis_PS_pr + Rho_dis_PS_li) +
            fXI_PF * (Rho_dis_PF_ch + Rho_dis_PF_pr + Rho_dis_PF_li) +
            f_p_bac * sum_decay

        du[23] = D_in * s_in[23] - D_out * X_su + Y_su * Rho_su - Rho_dec_su
        du[24] = D_in * s_in[24] - D_out * X_aa + Y_aa * Rho_aa - Rho_dec_aa
        du[25] = D_in * s_in[25] - D_out * X_fa + Y_fa * Rho_fa - Rho_dec_fa
        du[26] =
            D_in * s_in[26] - D_out * X_c4 + Y_c4 * Rho_c4_va + Y_c4 * Rho_c4_bu - Rho_dec_c4
        du[27] = D_in * s_in[27] - D_out * X_pro + Y_pro * Rho_pro - Rho_dec_pro
        du[28] = D_in * s_in[28] - D_out * X_ac + Y_ac * Rho_ac - Rho_dec_ac
        du[29] = D_in * s_in[29] - D_out * X_h2 + Y_h2 * Rho_h2 - Rho_dec_h2

        du[30] = D_in * s_in[30] - D_out * S_cation
        du[31] = D_in * s_in[31] - D_out * S_anion
        du[32] = D_in * s_in[32] - D_out * S_va_ion - Rho_A_va
        du[33] = D_in * s_in[33] - D_out * S_bu_ion - Rho_A_bu
        du[34] = D_in * s_in[34] - D_out * S_pro_ion - Rho_A_pro
        du[35] = D_in * s_in[35] - D_out * S_ac_ion - Rho_A_ac
        du[36] = D_in * s_in[36] - D_out * S_hco3 - Rho_A_co2
        du[37] = D_in * s_in[37] - D_out * S_nh3 - Rho_A_IN

        du[38] = Rho_T_h2 * pa.RT / 16.0 - p_gas_h2 / pTOTAL * Rho_T_11
        du[39] = Rho_T_ch4 * pa.RT / 64.0 - p_gas_ch4 / pTOTAL * Rho_T_11
        du[40] = Rho_T_co2 * pa.RT - p_gas_co2 / pTOTAL * Rho_T_11
        du[41] =
            pa.RT / 16.0 * Rho_T_h2 +
            pa.RT / 64.0 * Rho_T_ch4 +
            pa.RT * Rho_T_co2 -
            Rho_T_11
    end
    return nothing
end

# ---------------------------------------------------------------------------
# Integration entry point (called from Python)
# ---------------------------------------------------------------------------

"""
    integrate_step(pvec, u0, t0, t1, rtol, atol, dtmax)

Integrate the ADM1da system from `t0` to `t1` with Rodas4P — the same
Rosenbrock method ADM1jl uses for ADM1.

Returns `(y_end, success, n_steps)`.
"""
function integrate_step(
    pvec::AbstractVector,
    u0::AbstractVector,
    t0::Float64,
    t1::Float64,
    rtol::Float64,
    atol::Float64,
    dtmax::Float64,
)
    # Thin wrapper only. Converting here keeps the expensive body compiled
    # exactly once for Vector{Float64}; without the split, every new argument
    # type (juliacall hands over a PyArray) re-specialised the whole solver,
    # and that specialisation is what the precompile workload cannot cover.
    return _integrate_step(Vector{Float64}(pvec), Vector{Float64}(u0), t0, t1, rtol, atol, dtmax)
end

function _integrate_step(
    pvec::Vector{Float64},
    u0::Vector{Float64},
    t0::Float64,
    t1::Float64,
    rtol::Float64,
    atol::Float64,
    dtmax::Float64,
)
    pa = unpack(pvec)
    s_in = pvec[(NP + 1):(NP + N_INFLUENT)]
    prob = ODEProblem((du, u, p, t) -> adm1da_rhs!(du, u, p[1], p[2], t), u0, (t0, t1), (pa, s_in))
    sol = solve(
        prob,
        Rodas4P();
        abstol = atol,
        reltol = rtol,
        dtmax = dtmax,
        save_everystep = false,
        maxiters = 10_000_000,
    )
    y_end = Vector{Float64}(sol.u[end])
    ok = sol.retcode == SciMLBase.ReturnCode.Success
    return (y_end, ok, length(sol.t))
end

"""
    integrate_trace(pvec, u0, t0, t1, rtol, atol, dtmax)

Integrate like [`integrate_step`](@ref) but keep **every accepted internal
step**. Used by the computation-trace visualization, which replays the solver's
actual march through time rather than a resampled curve.

Returns `(t::Vector{Float64}, u::Vector{Vector{Float64}}, success)`.
"""
function integrate_trace(
    pvec::AbstractVector,
    u0::AbstractVector,
    t0::Float64,
    t1::Float64,
    rtol::Float64,
    atol::Float64,
    dtmax::Float64,
)
    return _integrate_trace(Vector{Float64}(pvec), Vector{Float64}(u0), t0, t1, rtol, atol, dtmax)
end

function _integrate_trace(
    pvec::Vector{Float64},
    u0::Vector{Float64},
    t0::Float64,
    t1::Float64,
    rtol::Float64,
    atol::Float64,
    dtmax::Float64,
)
    pa = unpack(pvec)
    s_in = pvec[(NP + 1):(NP + N_INFLUENT)]
    prob = ODEProblem((du, u, p, t) -> adm1da_rhs!(du, u, p[1], p[2], t), u0, (t0, t1), (pa, s_in))
    sol = solve(
        prob,
        Rodas4P();
        abstol = atol,
        reltol = rtol,
        dtmax = dtmax,
        save_everystep = true,
        maxiters = 10_000_000,
    )
    ts = Vector{Float64}(sol.t)
    us = Vector{Vector{Float64}}([Vector{Float64}(u) for u in sol.u])
    return (ts, us, sol.retcode == SciMLBase.ReturnCode.Success)
end

"""
    rhs_eval(pvec, u)

Evaluate the right-hand side once (used by the parity check against the
Python implementation).
"""
function rhs_eval(pvec::AbstractVector, u::AbstractVector)
    return _rhs_eval(Vector{Float64}(pvec), Vector{Float64}(u))
end

function _rhs_eval(pvec::Vector{Float64}, u::Vector{Float64})
    pa = unpack(pvec)
    s_in = pvec[(NP + 1):(NP + N_INFLUENT)]
    du = zeros(Float64, N_STATE)
    adm1da_rhs!(du, u, pa, s_in, 0.0)
    return du
end

"""
    q_s_loss_eval(pvec, u)

Sludge-volume loss rate at state `u` [m³/d].
"""
function q_s_loss_eval(pvec::AbstractVector, u::AbstractVector)
    return _q_s_loss_eval(Vector{Float64}(pvec), Vector{Float64}(u))
end

function _q_s_loss_eval(pvec::Vector{Float64}, u::Vector{Float64})
    return q_s_loss_of(u, unpack(pvec))
end

"""
    solver_name()

Identifier for logging/telemetry.
"""
solver_name() = "ADM1jl/Rodas4P"


# ---------------------------------------------------------------------------
# Precompile workload
#
# Julia caches native code per concrete argument type. Running one real
# integration at package-precompile time therefore removes the first-call JIT
# that used to cost ~5.7 s per process. The numbers below are a default plant's
# parameter vector and state, generated by gen_package.py.
# ---------------------------------------------------------------------------

@compile_workload begin
    pvec = copy(W_PVEC)
    u0 = copy(W_U0)
    # Call the typed cores, not the AbstractVector wrappers: the cores are the
    # expensive part, and they are the exact methods the runtime hits after the
    # PyArray -> Vector conversion.
    _rhs_eval(pvec, u0)
    _q_s_loss_eval(pvec, u0)
    _integrate_step(pvec, u0, 0.0, 1.0e-3, 1.0e-6, 1.0e-8, 1.0e-3)
    _integrate_trace(pvec, u0, 0.0, 1.0e-3, 1.0e-6, 1.0e-8, 1.0e-3)
end

end # module
