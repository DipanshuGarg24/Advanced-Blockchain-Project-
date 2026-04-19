// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

/**
 * @title TrustedBPI v2
 * @author Dipanshu Garg & Nitesh Singh — IIT Bombay CS762
 *
 * Changes from v1:
 *   - Sender can settle (push model) — Bob gets money even if he's lazy
 *   - 96-hour expiry on unclaimed receiver funds
 *   - Proper collateral withdrawal (unused portion)
 *   - Settlement deduplication — if already settled, just returns true
 *   - RecordIssuance tracks what's been minted per user
 */
contract TrustedBPI {

    address public mintAuthority;
    uint256 public collateralRatio;       // 200 = 2x
    uint256 public penaltyBasisPoints;    // 1000 = 10%
    uint256 public constant CLAIM_EXPIRY = 96 hours;

    struct UserAccount {
        uint256 collateralLocked;
        uint256 tokensIssuedValue;        // total value of minted tokens
        uint256 tokensSpentValue;         // tokens used in settlements
        uint256 balance;                  // claimable balance
        bool isRegistered;
        bool isSlashed;
    }

    struct Settlement {
        bytes32 paymentId;
        address payer;
        address payee;
        uint256 amount;
        uint256 settledAt;
        uint256 expiresAt;                // payee must claim before this
        bool exists;
        bool payeeClaimed;               // has payee confirmed receipt
    }

    mapping(address => UserAccount) public accounts;
    mapping(bytes32 => Settlement) public settlements;
    mapping(bytes32 => bool) public spentSerials;

    uint256 public totalCollateralLocked;
    uint256 public totalSettled;
    uint256 public totalFraudsDetected;

    // ── Events ──

    event UserRegistered(address indexed user);
    event CollateralDeposited(address indexed user, uint256 amount, uint256 total);
    event CollateralWithdrawn(address indexed user, uint256 amount);
    event TokensIssued(address indexed user, uint256 value);
    event PaymentSettled(
        bytes32 indexed paymentId, address indexed payer,
        address indexed payee, uint256 amount
    );
    event PayeeClaimed(bytes32 indexed paymentId, address indexed payee, uint256 amount);
    event SettlementExpired(bytes32 indexed paymentId, address indexed payer, uint256 amount);
    event DoubleSpendDetected(
        bytes32 indexed tokenSerial, address indexed cheater, uint256 amountSlashed
    );

    // ── Modifiers ──

    modifier onlyMint() {
        require(msg.sender == mintAuthority, "Only Mint");
        _;
    }

    modifier registered(address u) {
        require(accounts[u].isRegistered, "Not registered");
        _;
    }

    constructor(uint256 _ratio, uint256 _penalty) {
        mintAuthority = msg.sender;
        collateralRatio = _ratio;
        penaltyBasisPoints = _penalty;
    }

    // ═══════════════════════════════════════
    // USER FUNCTIONS
    // ═══════════════════════════════════════

    function register() external {
        require(!accounts[msg.sender].isRegistered, "Already registered");
        accounts[msg.sender].isRegistered = true;
        emit UserRegistered(msg.sender);
    }

    function deposit() external payable registered(msg.sender) {
        require(msg.value > 0, "Must send ETH");
        require(!accounts[msg.sender].isSlashed, "Slashed");
        accounts[msg.sender].collateralLocked += msg.value;
        totalCollateralLocked += msg.value;
        emit CollateralDeposited(msg.sender, msg.value, accounts[msg.sender].collateralLocked);
    }

    /**
     * @notice Withdraw unused collateral
     * Withdrawable = collateral - (tokensIssued - tokensSpent) * ratio / 100
     * Only the portion NOT backing outstanding (unspent) tokens can be withdrawn
     */
    function withdrawCollateral(uint256 amount) external registered(msg.sender) {
        require(!accounts[msg.sender].isSlashed, "Slashed");
        UserAccount storage a = accounts[msg.sender];

        uint256 outstandingTokens = 0;
        if (a.tokensIssuedValue > a.tokensSpentValue) {
            outstandingTokens = a.tokensIssuedValue - a.tokensSpentValue;
        }
        uint256 minLocked = outstandingTokens * collateralRatio / 100;
        uint256 available = 0;
        if (a.collateralLocked > minLocked) {
            available = a.collateralLocked - minLocked;
        }

        require(amount <= available, "Exceeds withdrawable");
        a.collateralLocked -= amount;
        totalCollateralLocked -= amount;
        payable(msg.sender).transfer(amount);
        emit CollateralWithdrawn(msg.sender, amount);
    }

    /// @notice Withdraw earned balance (from received payments)
    function withdrawBalance(uint256 amount) external registered(msg.sender) {
        require(accounts[msg.sender].balance >= amount, "Insufficient");
        accounts[msg.sender].balance -= amount;
        payable(msg.sender).transfer(amount);
    }

    // ═══════════════════════════════════════
    // MINT FUNCTIONS
    // ═══════════════════════════════════════

    function recordIssuance(address user, uint256 value)
        external onlyMint registered(user)
    {
        uint256 maxIssuance = accounts[user].collateralLocked * 100 / collateralRatio;
        uint256 outstanding = accounts[user].tokensIssuedValue - accounts[user].tokensSpentValue;
        require(outstanding + value <= maxIssuance, "Exceeds collateral limit");
        accounts[user].tokensIssuedValue += value;
        emit TokensIssued(user, value);
    }

    /**
     * @notice Settle a payment — can be called by EITHER payer (Alice) or Mint
     * If already settled, returns silently (deduplication)
     *
     * Flow: payer's collateral → held for payee → payee claims or it expires
     */
    function settle(
        bytes32 paymentId, address payer, address payee,
        uint256 amount, bytes32 tokenSerial
    ) external onlyMint {
        // Deduplication — if already settled, skip
        if (settlements[paymentId].exists) {
            return;
        }

        require(!spentSerials[tokenSerial], "Serial already spent");
        require(accounts[payer].collateralLocked >= amount, "Insufficient collateral");

        spentSerials[tokenSerial] = true;

        // Move from payer's collateral to payee's claimable balance
        accounts[payer].collateralLocked -= amount;
        accounts[payer].tokensSpentValue += amount;
        totalCollateralLocked -= amount;

        // Credit payee immediately
        accounts[payee].balance += amount;
        totalSettled += amount;

        settlements[paymentId] = Settlement({
            paymentId: paymentId,
            payer: payer,
            payee: payee,
            amount: amount,
            settledAt: block.timestamp,
            expiresAt: block.timestamp + CLAIM_EXPIRY,
            exists: true,
            payeeClaimed: false
        });

        emit PaymentSettled(paymentId, payer, payee, amount);
    }

    /**
     * @notice Payee confirms receipt — marks the settlement as fully complete
     * If payee doesn't call this within 96 hours, payer can reclaim
     */
    function confirmReceipt(bytes32 paymentId) external {
        Settlement storage s = settlements[paymentId];
        require(s.exists, "Settlement not found");
        require(msg.sender == s.payee, "Not the payee");
        require(!s.payeeClaimed, "Already claimed");
        require(block.timestamp <= s.expiresAt, "Expired");

        s.payeeClaimed = true;
        emit PayeeClaimed(paymentId, s.payee, s.amount);
    }

    /**
     * @notice If payee didn't confirm within 96 hours, payer reclaims
     */
    function reclaimExpired(bytes32 paymentId) external {
        Settlement storage s = settlements[paymentId];
        require(s.exists, "Not found");
        require(msg.sender == s.payer || msg.sender == mintAuthority, "Not authorized");
        require(!s.payeeClaimed, "Already claimed");
        require(block.timestamp > s.expiresAt, "Not expired yet");

        // Return funds to payer
        accounts[s.payee].balance -= s.amount;
        accounts[s.payer].collateralLocked += s.amount;
        totalCollateralLocked += s.amount;
        totalSettled -= s.amount;

        emit SettlementExpired(paymentId, s.payer, s.amount);
    }

    /**
     * @notice Report double-spend with cryptographic proof
     */
    function reportDoubleSpend(
        bytes32 tokenSerial, address cheater,
        bytes32 payment1Hash, bytes32 payment2Hash,
        address victim1, address victim2, uint256 tokenValue
    ) external onlyMint {
        require(payment1Hash != payment2Hash, "Same payment");
        require(accounts[cheater].isRegistered, "Unknown cheater");

        uint256 penalty = tokenValue * penaltyBasisPoints / 10000;
        uint256 totalSlash = tokenValue * 2 + penalty;
        if (totalSlash > accounts[cheater].collateralLocked) {
            totalSlash = accounts[cheater].collateralLocked;
        }

        accounts[cheater].collateralLocked -= totalSlash;
        accounts[cheater].isSlashed = true;
        totalCollateralLocked -= totalSlash;

        uint256 perVictim = tokenValue;
        if (perVictim * 2 > totalSlash) perVictim = totalSlash / 2;
        accounts[victim1].balance += perVictim;
        accounts[victim2].balance += perVictim;

        totalFraudsDetected++;
        emit DoubleSpendDetected(tokenSerial, cheater, totalSlash);
    }

    // ═══════════════════════════════════════
    // VIEW FUNCTIONS
    // ═══════════════════════════════════════

    function getAccountInfo(address user) external view returns (
        uint256 collateral, uint256 tokensIssued, uint256 tokensSpent,
        uint256 balance, bool isRegistered, bool slashed
    ) {
        UserAccount storage a = accounts[user];
        return (a.collateralLocked, a.tokensIssuedValue, a.tokensSpentValue,
                a.balance, a.isRegistered, a.isSlashed);
    }

    function getWithdrawableCollateral(address user) external view returns (uint256) {
        UserAccount storage a = accounts[user];
        uint256 outstanding = 0;
        if (a.tokensIssuedValue > a.tokensSpentValue) {
            outstanding = a.tokensIssuedValue - a.tokensSpentValue;
        }
        uint256 minLocked = outstanding * collateralRatio / 100;
        if (a.collateralLocked > minLocked) {
            return a.collateralLocked - minLocked;
        }
        return 0;
    }

    function getSystemStats() external view returns (
        uint256 _totalCollateral, uint256 _totalSettled, uint256 _totalFrauds
    ) {
        return (totalCollateralLocked, totalSettled, totalFraudsDetected);
    }

    function getSettlementInfo(bytes32 paymentId) external view returns (
        address payer, address payee, uint256 amount,
        uint256 settledAt, uint256 expiresAt, bool payeeClaimed
    ) {
        Settlement storage s = settlements[paymentId];
        require(s.exists, "Not found");
        return (s.payer, s.payee, s.amount, s.settledAt, s.expiresAt, s.payeeClaimed);
    }
}
