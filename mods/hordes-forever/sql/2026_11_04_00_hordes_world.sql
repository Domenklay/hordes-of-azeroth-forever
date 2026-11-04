-- Dedicated entries: kills cannot advance ordinary world kill objectives.
INSERT IGNORE INTO `creature_template`
 (`entry`,`name`,`subname`,`faction`,`unit_class`,`type`,`BaseAttackTime`,`RangeAttackTime`,`RegenHealth`,`ExperienceModifier`,`ScriptName`)
 VALUES
 (900950,'Порождение Орды','Hordes Forever',14,1,7,1200,1200,0,0,'hordes_forever_enemy'),
 (900951,'Владыка арены','Hordes Forever',14,1,7,1200,1200,0,0,'hordes_forever_enemy');
INSERT IGNORE INTO `creature_template_model`
 (`CreatureID`,`Idx`,`CreatureDisplayID`,`DisplayScale`,`Probability`,`VerifiedBuild`)
 SELECT 900950,`Idx`,`CreatureDisplayID`,`DisplayScale`,`Probability`,`VerifiedBuild`
 FROM `creature_template_model` WHERE `CreatureID`=6;
INSERT IGNORE INTO `creature_template_model`
 (`CreatureID`,`Idx`,`CreatureDisplayID`,`DisplayScale`,`Probability`,`VerifiedBuild`)
 SELECT 900951,`Idx`,`CreatureDisplayID`,`DisplayScale`*2,`Probability`,`VerifiedBuild`
 FROM `creature_template_model` WHERE `CreatureID`=6;
